import json
from dataclasses import replace
import pytest
from tests.checkpoint_d_fixture import c_fixture,d_fixture
from domains.jobs.evidence_adapter import JobEvidence,job_scope
from evidence.contracts import Evidence
from evidence.service import EvidenceService
from worker.application_executor import ApplicationExecutor
from worker.final_submission import FinalSubmissionExecutor
from domains.jobs import make_processor
from tests.test_application_generation import FakeGateway,SequenceGateway,valid_payload
from skills.application_generation.skill import generate_application_draft
from database.evidence_migrations import legacy_digest


@pytest.mark.parametrize('state',['UNVERIFIED','PARTIALLY_VERIFIED','VERIFIED','DISPUTED','STALE'])
def test_job_lifecycle_independent_and_legacy_unchanged(d_fixture,state):
    f=d_fixture;a=JobEvidence(f.store)
    s=a.shared
    for fact_id in f.fact_ids:
        e=s.create(Evidence('DOCUMENTED','jobs','document','ref','content',verification=state,verification_basis='review'))
        a.link(fact_id,e)
    expected=[] if state in {'DISPUTED','STALE'} else [f.fact_ids[1]]
    assert [x['id'] for x in a.confirmed()]==expected
    with f.store._connect() as con:assert legacy_digest(con)==f.before


def test_explicit_adaptation_never_promotes_or_invents_time(d_fixture):
    f=d_fixture;a=JobEvidence(f.store)
    e=a.adapt(f.fact_ids[1],truth='OBSERVED')
    record=a.shared.get(e)
    assert record['verification']=='UNVERIFIED' and record['time']['source_at'] is None
    assert record['source_ref']==str(f.fact_ids[1])
    assert a.confirmed()[0]['id']==f.fact_ids[1]
    f.store.update_candidate_fact(f.fact_ids[1],'REVOKED')
    assert not a.confirmed()
    assert f.store.application_snapshots('app-fixture')[0]['cover_letter_text']=='Synthetic preserve'


def test_changed_content_cannot_reuse_old_support(d_fixture):
    f=d_fixture;a=JobEvidence(f.store)
    e=a.adapt(f.fact_ids[1],truth='DOCUMENTED')
    with f.store._connect() as con:con.execute('UPDATE candidate_facts SET text=? WHERE id=?',('New unsupported text',f.fact_ids[1]))
    assert a.confirmed()==[]
    with pytest.raises(ValueError):a.link(f.fact_ids[1],e)


def test_generation_and_form_recheck_adverse_evidence(d_fixture):
    f=d_fixture;a=JobEvidence(f.store)
    e=a.adapt(f.fact_ids[1],truth='DOCUMENTED')
    payload=valid_payload();payload['claims'][0]['fact_id']=f.fact_ids[1]
    generate_application_draft(FakeGateway(json.dumps(payload)),{'title':'SOC Analyst'},{'facts':a.confirmed()})
    executor=ApplicationExecutor(f.store,candidate_fact_provider=a.confirmed)
    fields=[{'label':'Skill','value':'Nmap','fact_id':f.fact_ids[1]}]
    assert executor.validate_fields(fields)==[]
    a.shared.verify(e,'DISPUTED','conflict-review')
    with pytest.raises(ValueError):generate_application_draft(FakeGateway(json.dumps(payload)),{'title':'SOC Analyst'},{'facts':a.confirmed()})
    assert executor.validate_fields(fields)


def test_factory_draft_history_and_ledger(d_fixture,monkeypatch):
    f=d_fixture;a=JobEvidence(f.store);e=a.adapt(f.fact_ids[1],truth='DOCUMENTED')
    payload=valid_payload();payload['claims'][0]['fact_id']=f.fact_ids[1]
    command=f.store.queue_command('synthetic draft')
    plan={'actions':[{'action':'draft_cv','payload':{'job_id':'job-fixture'}}]}
    processor=make_processor(f.store,SequenceGateway([json.dumps(plan),json.dumps(payload)]))
    # Real generation, validation, guarded files and history in isolated state.
    result=processor.process_command(command,'synthetic draft')
    assert not result['approvals']
    observer=processor.ledger_observer
    assert observer.last_error is None
    events=observer.ledger.events(observer.last_correlation)
    assert {'INTENT','POLICY','OUTCOME'}<={x['phase'] for x in events}
    assert any(x['policy_decision']=='ALLOW' for x in events)
    references=[r for x in events for r in x['references']]
    assert {'command','audit','application','snapshot','application_event'}<={x['kind'] for x in references}
    assert any(e in x['evidence_ids'] for x in events)


def test_unmigrated_factory_compatible(c_fixture):
    f=c_fixture
    with job_scope():assert JobEvidence(f.store).confirmed()==f.store.candidate_facts(status='USER_CONFIRMED')


def test_final_preflight_revalidates_provider(d_fixture,monkeypatch):
    f=d_fixture;a=JobEvidence(f.store);e=a.adapt(f.fact_ids[1],truth='DOCUMENTED')
    url='https://synthetic.test/apply'
    monkeypatch.setattr(f.store,'sources',lambda:[{'url':url,'verification_status':'APPROVED'}])
    fields=[{'label':'Skill','value':'Nmap','fact_id':f.fact_ids[1]}]
    f.store.add_application_snapshot({'application_id':'app-fixture','stage':'FORM_FILLED','source_url':url,'form_fields':fields})
    executor=FinalSubmissionExecutor(f.store,candidate_fact_provider=a.confirmed)
    assert executor.preflight('app-fixture',url).ok
    a.shared.verify(e,'STALE','expiry-review')
    assert not executor.preflight('app-fixture',url).ok
