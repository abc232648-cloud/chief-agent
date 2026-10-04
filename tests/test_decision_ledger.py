import json
import sqlite3
import pytest
from tests.checkpoint_d_fixture import c_fixture,d_fixture
from domains.jobs.ledger_adapter import JobLedger
from domains.jobs import make_processor
from decision_ledger.service import DecisionLedger
from tests.test_application_generation import FakeGateway
from security.permissions import worker_context
from domains.contracts import AgentDefinition


def entry(**kwargs):
    values=dict(event_key='event-1',correlation_id='correlation-1',action='submit_application',phase='INTENT',outcome='NOT_ATTEMPTED',rationale='USER_REVIEW',risk='HIGH_IMPACT',policy_decision='ASK')
    values.update(kwargs);return values


def test_idempotent_append_only_reference_contract(d_fixture):
    f=d_fixture;j=JobLedger(f.store);l=j.ledger
    refs=[{'domain':'jobs','kind':k,'id':str(v)} for k,v in [('application','app-fixture'),('snapshot',f.snapshot),('application_event',f.event)]]
    first=l.append(**entry(references=refs))
    assert l.append(**entry(references=refs))==first
    with pytest.raises(ValueError):l.append(**entry(outcome='DIFFERENT'))
    with f.store._connect() as con:
        with pytest.raises(sqlite3.IntegrityError):con.execute('DELETE FROM decision_ledger')
    events=l.events('correlation-1')
    assert len(events)==1 and events[0]['outcome']=='NOT_ATTEMPTED'
    assert not hasattr(l,'execute') and not hasattr(l,'replay')


@pytest.mark.parametrize('ref',[{'domain':'farming','kind':'application','id':'app-fixture'}, {'domain':'jobs','kind':'application','id':'missing'}, {'domain':'jobs','kind':'application','id':'app-fixture','payload':'secret'}])
def test_invalid_references_rejected(d_fixture,ref):
    with pytest.raises((PermissionError,LookupError)):JobLedger(d_fixture.store).ledger.append(**entry(references=[ref]))


def test_domain_events_are_private(d_fixture):
    l=JobLedger(d_fixture.store).ledger;l.append(**entry())
    with worker_context(AgentDefinition('other','farming',frozenset({'farming.records.read'}))):
        with pytest.raises(PermissionError):l.events('correlation-1')
        other=DecisionLedger(d_fixture.store,'farming','farming.records.read')
        assert other.events('correlation-1')==[]


def test_actual_policy_approval_and_failure_no_replay(d_fixture,monkeypatch):
    f=d_fixture
    plan={'actions':[{'action':'submit_application','payload':{'application_id':'app-fixture','url':'https://synthetic.test','secret':'never-copy'}}]}
    command=f.store.queue_command('private instruction never-copy')
    processor=make_processor(f.store,FakeGateway(json.dumps(plan)))
    result=processor.process_command(command,'private instruction never-copy')
    obs=processor.ledger_observer
    events=obs.ledger.events(obs.last_correlation)
    assert any(e['policy_decision']=='ASK' for e in events)
    assert any(e['approval']=='PENDING' for e in events)
    assert 'never-copy' not in json.dumps(events) and 'https://' not in json.dumps(events)
    action=result['approvals'][0];f.store.resolve_action(action,'APPROVED')
    calls=[]
    monkeypatch.setattr(processor.final_submission_executor,'submit',lambda *a,**kw:calls.append(kw) or {'status':'SUBMITTED','application_id':'app-fixture'})
    monkeypatch.setattr(obs.ledger,'append',lambda **kw:(_ for _ in ()).throw(OSError('synthetic ledger failure')))
    assert processor.process_approved_action(action)['status']=='SUBMITTED'
    assert len(calls)==1 and calls[0]['approved']
    assert obs.last_error=='OSError'
    assert processor.process_approved_action(action)['status']=='FAILED'
    assert len(calls)==1
    assert f.store.get_action(action)['status']=='DONE'


def test_farm_command_cannot_enter_job_processor(d_fixture):
    f=d_fixture;command=f.store.queue_command('farm only')
    with f.store._connect() as con:con.execute("INSERT INTO domain_requests VALUES(?,'farming','read','{}')",(command,))
    with pytest.raises(PermissionError):make_processor(f.store,FakeGateway('{}')).process_command(command,'farm only')
