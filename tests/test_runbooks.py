from dataclasses import replace
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from tests.checkpoint_e_fixture import d_base,e_fixture
from runbooks.contracts import Definition,Step,Binding
from runbooks.engine import RunbookEngine
from application.composition import default_catalogs
from policy.contracts import ActionRisk,PolicyPack,PolicyRule,Precedence,Outcome
from policy.engine import PolicyEngine
from domains.jobs.evidence_adapter import job_scope
from evidence.contracts import Evidence
from operations.time_integrity import TimeReceipt,ClockQuality,utc_now,utc_text
from security.permissions import worker_context
from domains.contracts import AgentDefinition


def make_engine(f,*,handler=None,decision=Outcome.ALLOW,capability='jobs.execute',external=False,risk=ActionRisk.READ,prerequisite=None,approval=None,version='1.0.0'):
    binding=Binding('synthetic.read',capability,risk,version,'synthetic-source-v1',handler or (lambda refs:'OK'),external)
    pack=PolicyPack('fixture.policy','1.0.0',Precedence.CHIEF,(PolicyRule('synthetic.read',decision,'Fixture policy'),))
    return RunbookEngine(f.store,'jobs','jobs.execute',default_catalogs().capabilities,PolicyEngine((pack,)),(binding,),prerequisite=prerequisite,approval=approval)


def define(engine,steps=None,version='1.0.0'):
    definition=Definition('jobs','synthetic',version,'first',steps or (Step('first','synthetic.read'),))
    engine.register(definition)
    return definition


@pytest.mark.parametrize('branch',['YES','NO'])
def test_deterministic_branching_and_ledger(e_fixture,branch):
    calls=[]
    engine=make_engine(e_fixture,handler=lambda refs:calls.append(refs) or (branch if len(calls)==1 else 'OK'))
    steps=(Step('first','synthetic.read',(('YES','yes'),('NO','no'))),Step('yes','synthetic.read'),Step('no','synthetic.read'))
    define(engine,steps);run=engine.start('synthetic','1.0.0')
    state=engine.advance(run)
    assert state['state']=='READY' and state['step']==branch.lower()
    assert engine.advance(run)['state']=='COMPLETED' and len(calls)==2
    assert engine.advance(run)['state']=='COMPLETED' and len(calls)==2
    events=engine.ledger.events(run)
    assert [e['phase'] for e in events]==['INTENT','OUTCOME','INTENT','OUTCOME']
    assert all(e['references'][0]['kind']=='sop_run' and e['policy_ref'].startswith('policy-set:') for e in events)


@pytest.mark.parametrize('reason',['prerequisite','evidence','policy','capability','external','high_risk'])
def test_preconditions_and_authority_never_bypassed(e_fixture,reason):
    calls=[]
    engine=make_engine(e_fixture,handler=lambda refs:calls.append(refs) or 'OK',decision=Outcome.BLOCK if reason=='policy' else Outcome.ALLOW,
        capability='chief.agent_controls' if reason=='capability' else 'jobs.execute',external=reason=='external',risk=ActionRisk.HIGH_IMPACT if reason=='high_risk' else ActionRisk.READ)
    step=Step('first','synthetic.read',prerequisites=('required',) if reason=='prerequisite' else (),evidence_required=reason=='evidence')
    define(engine,(step,));run=engine.start('synthetic','1.0.0')
    assert engine.advance(run,approval_ref='not-authority')['state'] in {'BLOCKED','REVIEW'}
    assert calls==[]


@pytest.mark.parametrize('via_policy',[False,True])
def test_approval_pause_resume_scoped_and_rechecked(e_fixture,via_policy):
    calls=[];approved=set()
    engine=make_engine(e_fixture,handler=lambda refs:calls.append(refs) or 'OK',decision=Outcome.ASK if via_policy else Outcome.ALLOW,
        approval=lambda ref,run,step,digest:(ref,run,step,digest) in approved)
    definition=define(engine,(Step('first','synthetic.read',approval_required=not via_policy),))
    run=engine.start('synthetic','1.0.0')
    assert engine.advance(run)['state']=='WAITING_APPROVAL'
    assert engine.advance(run,approval_ref='wrong')['state']=='WAITING_APPROVAL'
    approved.add(('approval-1',run,'first',definition.digest))
    other=engine.start('synthetic','1.0.0')
    assert engine.advance(other,approval_ref='approval-1')['state']=='WAITING_APPROVAL'
    assert engine.advance(run,approval_ref='approval-1')['state']=='COMPLETED' and len(calls)==1
    records=engine.ledger.events(run)
    assert any(e['approval']=='APPROVED' and any(r['kind']=='sop_approval' for r in e['references']) for e in records)
    with e_fixture.store._connect() as con:
        assert con.execute('SELECT approval_ref FROM sop_events WHERE run_id=? AND state=?',(run,'RUNNING')).fetchone()[0]=='approval-1'


def test_approval_cannot_override_new_hard_policy(e_fixture):
    calls=[]
    engine=make_engine(e_fixture,handler=lambda refs:calls.append(1) or 'OK',decision=Outcome.ASK,approval=lambda *args:True)
    define(engine);run=engine.start('synthetic','1.0.0')
    assert engine.advance(run)['state']=='WAITING_APPROVAL'
    engine.policy=PolicyEngine((PolicyPack('hard','2.0.0',Precedence.HARD_SAFETY_SECURITY,(PolicyRule('synthetic.read',Outcome.BLOCK,'Forbidden',True),)),))
    assert engine.advance(run,approval_ref='approved')['state']=='BLOCKED' and not calls


def test_version_pinning_immutable_history_and_changed_binding(e_fixture):
    engine=make_engine(e_fixture);first=define(engine)
    run=engine.start('synthetic','1.0.0')
    define(engine,(Step('first','synthetic.read',prerequisites=('new',)),),'2.0.0')
    with pytest.raises(ValueError):define(engine,(Step('first','synthetic.read',approval_required=True),))
    assert engine.get(run)['digest']==first.digest and engine.advance(run)['state']=='COMPLETED'
    run2=engine.start('synthetic','1.0.0')
    restarted=make_engine(e_fixture,version='2.0.0')
    assert restarted.advance(run2)['state']=='REVIEW'
    with e_fixture.store._connect() as con:
        for table in ('sop_definitions','sop_events'):
            with pytest.raises(sqlite3.IntegrityError):con.execute('DELETE FROM '+table)


def test_restart_ready_version_is_reproducible(e_fixture):
    first=make_engine(e_fixture);definition=define(first)
    run=first.start('synthetic','1.0.0')
    restarted=make_engine(e_fixture)
    assert restarted.get(run)['digest']==definition.digest
    assert restarted.advance(run)['state']=='COMPLETED'


def test_interrupted_handler_never_replayed(e_fixture):
    calls=[]
    def interrupted(refs):calls.append(1);raise KeyboardInterrupt('synthetic crash after possible effect')
    engine=make_engine(e_fixture,handler=interrupted);define(engine);run=engine.start('synthetic','1.0.0')
    with pytest.raises(KeyboardInterrupt):engine.advance(run)
    assert engine.get(run)['state']=='RUNNING'
    restarted=make_engine(e_fixture,handler=interrupted)
    assert restarted.advance(run)['state']=='RUNNING'
    assert restarted.recover(run)['state']=='REVIEW'
    assert restarted.advance(run)['state']=='REVIEW' and calls==[1]


@pytest.mark.parametrize('phase',['INTENT','OUTCOME'])
def test_ledger_failure_never_replays(e_fixture,monkeypatch,phase):
    calls=[];engine=make_engine(e_fixture,handler=lambda refs:calls.append(1) or 'OK')
    define(engine);run=engine.start('synthetic','1.0.0');append=engine.ledger.append
    def fail(**kwargs):
        if kwargs['phase']==phase:raise OSError('synthetic ledger failure')
        return append(**kwargs)
    monkeypatch.setattr(engine.ledger,'append',fail)
    if phase=='INTENT':assert engine.advance(run)['state']=='REVIEW' and not calls
    else:
        with pytest.raises(OSError):engine.advance(run)
        assert engine.recover(run)['state']=='REVIEW' and calls==[1]
    assert engine.advance(run)['state']=='REVIEW'


def test_local_failure_branch_rechecks_prerequisite(e_fixture):
    calls=[]
    def handler(refs):calls.append(1);raise ValueError('local failure')
    engine=make_engine(e_fixture,handler=handler)
    define(engine,(Step('first','synthetic.read',failure_next='recover'),Step('recover','synthetic.read',prerequisites=('manual-review',))))
    run=engine.start('synthetic','1.0.0')
    assert engine.advance(run)['step']=='recover'
    assert engine.advance(run)['state']=='BLOCKED' and calls==[1]


def test_quality_and_evidence_rechecked_after_approval_pause(e_fixture):
    calls=[];engine=make_engine(e_fixture,handler=lambda refs:calls.append(1) or 'OK',decision=Outcome.ASK,approval=lambda *args:True)
    define(engine,(Step('first','synthetic.read',evidence_required=True),))
    now=utc_text(utc_now())
    evidence=engine.evidence.create(Evidence('DOCUMENTED','jobs','document','synthetic-ref','synthetic-content',time=TimeReceipt(now,now,ClockQuality(quality='ASSERTED')),verification='VERIFIED',verification_basis='review',confidence=1,reliability=1))
    run=engine.start('synthetic','1.0.0',evidence_ids=(evidence,))
    assert engine.advance(run)['state']=='WAITING_APPROVAL'
    engine.evidence.verify(evidence,'DISPUTED','later-review')
    assert engine.advance(run,approval_ref='approval')['state']=='REVIEW' and not calls


def test_domain_isolation_and_definition_cannot_grant(e_fixture):
    engine=make_engine(e_fixture);define(engine);run=engine.start('synthetic','1.0.0')
    with worker_context(AgentDefinition('other','other',frozenset({'other.read'}))):
        with pytest.raises(PermissionError):engine.get(run)
        other=RunbookEngine(e_fixture.store,'other','other.read',default_catalogs().capabilities,engine.policy,engine.bindings.values())
        with pytest.raises(LookupError):other.get(run)
        with pytest.raises(PermissionError):other.register(Definition('other','fake','1.0.0','first',(Step('first','synthetic.read'),)))


def test_concurrent_advance_claims_handler_once(e_fixture):
    started=threading.Event();release=threading.Event();calls=[]
    def handler(refs):calls.append(1);started.set();assert release.wait(10);return 'OK'
    engine=make_engine(e_fixture,handler=handler);define(engine);run=engine.start('synthetic','1.0.0')
    def invoke():
        with job_scope():return engine.advance(run)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(invoke);assert started.wait(10)
        try:assert engine.advance(run)['state']=='RUNNING'
        finally:release.set()
        assert future.result()['state']=='COMPLETED'
    assert calls==[1]


@pytest.mark.parametrize('case',['empty','missing','cycle','duplicate','version','code','unknown_action'])
def test_invalid_definitions_rejected(e_fixture,case):
    engine=make_engine(e_fixture)
    with pytest.raises((ValueError,TypeError)):
        if case=='empty':Definition('jobs','bad','1.0.0','a',())
        elif case=='missing':Definition('jobs','bad','1.0.0','a',(Step('a','synthetic.read',(('OK','missing'),)),))
        elif case=='cycle':Definition('jobs','bad','1.0.0','a',(Step('a','synthetic.read',(('OK','a'),)),))
        elif case=='duplicate':Definition('jobs','bad','1.0.0','a',(Step('a','synthetic.read'),Step('a','synthetic.read')))
        elif case=='version':Definition('jobs','bad','latest','a',(Step('a','synthetic.read'),))
        elif case=='code':Step('a','__import__("os").system("x")')
        else:engine.register(Definition('jobs','bad','1.0.0','a',(Step('a','unknown'),)))
