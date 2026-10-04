from datetime import timedelta
import pytest
from tests.checkpoint_f_fixture import e_base,f_fixture
from tests.test_identity import user
from identity.service import IdentityService
from operations.time_integrity import utc_now
from worker.command_processor import CommandProcessor
from worker.action_gate import PolicyGate,ActionRequest
from domains.jobs.evidence_adapter import JobEvidence,job_scope

def test_delegation_is_bounded_and_revocable(f_fixture):
    f=f_fixture;recipient,_,worker=user(f,'Worker');action=f.store.add_action('Synthetic','submit_application')
    with pytest.raises(PermissionError):f.identity.authorize(worker,'work.approve','jobs','action:'+str(action))
    grant=f.identity.grant(f.owner,recipient,'jobs',resource='action:'+str(action),seconds=60)
    assert f.identity.authorize(worker,'work.approve','jobs','action:'+str(action)).startswith('DELEGATION:')
    for permission,domain,resource in [('work.approve','jobs','action:999'),('work.approve','farming','action:'+str(action)),('installation.manage',None,None),('delegation.manage',None,None)]:
        with pytest.raises(PermissionError):f.identity.authorize(worker,permission,domain,resource)
    f.identity.approve_action(worker,action,'jobs');assert f.identity.approval_valid(action,'jobs')
    f.identity.revoke_grant(f.owner,grant);assert not f.identity.approval_valid(action,'jobs')
    with pytest.raises(ValueError):f.identity.grant(f.owner,recipient,'jobs',resource='action:1',seconds=3601)
    with pytest.raises(PermissionError):f.identity.grant(f.owner,recipient,'farming',resource='action:1')
    with pytest.raises(PermissionError):f.identity.approve_action(f.owner,action,'farming')

def test_emergency_is_pause_only_and_expires(f_fixture):
    f=f_fixture;identity,_,worker=user(f,'Worker');f.identity.grant(f.owner,identity,'jobs',emergency=True,seconds=60,reason_ref='safety-review')
    assert f.identity.authorize(worker,'safety.pause','jobs').startswith('EMERGENCY:')
    for permission in ('work.approve','controls.manage','installation.manage'):
        with pytest.raises(PermissionError):f.identity.authorize(worker,permission,'jobs','action:1')
    future=IdentityService(f.store,now=lambda:utc_now()+timedelta(minutes=2))
    with pytest.raises(PermissionError):future.authorize(worker,'safety.pause','jobs')
    with pytest.raises(ValueError):f.identity.grant(f.owner,identity,'jobs',emergency=True,seconds=901)

def test_identified_approval_never_bypasses_legacy_policy(f_fixture):
    f=f_fixture;action=f.store.add_action('Forbidden synthetic','pay_money')
    f.identity.approve_action(f.owner,action,'jobs')
    assert f.identity.approval_valid(action,'jobs')
    assert PolicyGate().decide(ActionRequest('pay_money',{})).decision.value=='BLOCK'
    result=CommandProcessor(f.store,None).process_approved_action(action)
    assert result['status']=='BLOCKED'
    with f.store._connect() as con:
        record=con.execute('SELECT * FROM human_action_approvals WHERE action_id=?',(action,)).fetchone()
        assert record['human_id']==f.owner.id and record['session_id']==f.owner.session_id
        assert con.execute("SELECT actor FROM audit_log WHERE category='policy' ORDER BY id DESC LIMIT 1").fetchone()[0]==f.owner.id

@pytest.mark.parametrize('failure',['legacy','mutated','logout','expired'])
def test_invalid_approval_cannot_execute(f_fixture,failure):
    f=f_fixture;action=f.store.add_action('Synthetic','fill_application_form');calls=[]
    if failure=='legacy':f.store.resolve_action(action,'APPROVED')
    else:f.identity.approve_action(f.owner,action,'jobs')
    if failure=='mutated':
        with f.store._connect() as con:con.execute("UPDATE actions SET payload_json=? WHERE id=?",('{"changed":true}',action))
    elif failure=='logout':f.identity.revoke(f.owner)
    elif failure=='expired':
        with f.store._connect() as con:con.execute("UPDATE human_sessions SET expires_at='2000-01-01T00:00:00Z'")
    class Executor:
        def prepare(self,*args,**kwargs):calls.append(1);return {'status':'FILLED'}
    assert CommandProcessor(f.store,None,application_executor=Executor()).process_approved_action(action)['status']=='BLOCKED'
    assert not calls

def test_owner_does_not_change_evidence_or_agent_capabilities(f_fixture):
    from security.permissions import worker_context
    from domains.contracts import AgentDefinition
    f=f_fixture
    with worker_context(AgentDefinition('unprivileged','jobs',frozenset())):
        assert PolicyGate().decide(ActionRequest('draft_cv',{})).decision.value=='BLOCK'
    with job_scope():
        evidence=JobEvidence(f.store);evidence.shared.verify(f.evidence_id,'DISPUTED','synthetic-review')
        assert evidence.confirmed()==[]
    assert f.identity.authorize(f.owner,'work.approve','jobs','action:1')=='ROLE'
