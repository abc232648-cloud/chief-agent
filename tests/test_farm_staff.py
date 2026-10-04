import uuid
import pytest
from domains.farming import staff
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD


def event(**changes):
    return {'event_id':str(uuid.uuid4()),'kind':'INCIDENT','reference':None,'expected_event':None,
            'assignee':None,'due_at':None,'text':'Synthetic issue','severity':'ROUTINE',**changes}


def people(d):
    s=IdentityService(d.store);owner=d.credentials['principal']
    a=s.create_user(owner,'worker-a',PASSWORD,'Worker',('farming',))
    b=s.create_user(owner,'worker-b',PASSWORD,'Worker',('farming',))
    return owner,a,s.login('worker-a',PASSWORD)[1],b,s.login('worker-b',PASSWORD)[1]


def test_task_completion_needs_supervisor_resolution_and_preserves_history(dashboard):
    d=dashboard;owner,uid,worker,_,other=people(d)
    task=event(kind='TASK',assignee=uid,due_at='2026-01-01T00:00:00Z')
    staff.append(d.store,owner,task)
    assert staff.overview(d.store,worker)['overdue_count']==1
    assert staff.overview(d.store,other)['items']==[]
    complete=event(kind='REPORT_COMPLETION',reference=task['event_id'],expected_event=task['event_id'])
    with pytest.raises(PermissionError):staff.append(d.store,other,complete)
    staff.append(d.store,worker,complete)
    with pytest.raises(PermissionError):staff.append(d.store,worker,event(kind='RESOLVE',reference=task['event_id'],expected_event=complete['event_id']))
    resolved=event(kind='RESOLVE',reference=task['event_id'],expected_event=complete['event_id'])
    staff.append(d.store,owner,resolved)
    result=staff.overview(d.store,worker)
    assert result['open_count']==0 and result['overdue_count']==0 and len(result['items'][0]['history'])==3
    assert result['daily_report_schedule']=='NOT_CONFIGURED' and result['timezone']=='Africa/Lagos'
    with pytest.raises(ValueError):staff.append(d.store,worker,event(kind='ACKNOWLEDGE',reference=task['event_id'],expected_event=resolved['event_id']))


def test_incident_retry_conflict_and_revoked_reporter(dashboard):
    d=dashboard;owner,uid,worker,_,_=people(d);p=event()
    staff.append(d.store,worker,p)
    assert staff.append(d.store,worker,p)['status']=='ALREADY_RECORDED'
    with pytest.raises(ValueError):staff.append(d.store,worker,{**p,'text':'Changed'})
    ack=event(kind='ACKNOWLEDGE',reference=p['event_id'],expected_event=p['event_id'])
    staff.append(d.store,owner,ack)
    with pytest.raises(ValueError):staff.append(d.store,worker,event(kind='ESCALATE',reference=p['event_id'],expected_event=p['event_id']))
    IdentityService(d.store).disable_user(owner,uid)
    with pytest.raises(PermissionError):staff.append(d.store,worker,event())


def test_no_cross_domain_assignee_and_worker_cannot_assign(dashboard):
    d=dashboard;owner,_,worker,_,_=people(d)
    uid=IdentityService(d.store).create_user(owner,'job-person',PASSWORD,'Worker',('jobs',))
    p=event(kind='TASK',assignee=uid,due_at='2026-01-01T00:00:00Z')
    with pytest.raises(ValueError):staff.append(d.store,owner,p)
    with pytest.raises(PermissionError):staff.append(d.store,worker,p)
