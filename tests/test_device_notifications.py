from datetime import timedelta
import pytest
from notifications import device_push as push, web_push
from control.notifications import push_subscriptions, remove_push_subscription
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text
from tests.test_web_push import SUBSCRIPTION, ENV
from tests.test_farm_tasks import staff, create_task, event
from tests.test_farm_incidents import incident
from tests.test_farm_setup import assign
from tests.test_identity_http import request
from domains.farming import tasks, incidents

DEVICE_ENV = {**ENV, **{'CHIEF_' + k: v for k, v in ENV.items() if k.startswith('WEB_PUSH_VAPID_')}}


def register(d, p, surface, suffix='', consent=True):
    return push.register(d.store, p, surface, {**SUBSCRIPTION, 'endpoint': SUBSCRIPTION['endpoint'] + suffix, 'after_timeout': consent}, DEVICE_ENV)


def deliver(d, sender=None):
    sent=[]
    result=push.dispatch_pending(d.store, sender=sender or (lambda sub, payload, config: sent.append((sub['principal_id'],payload))), env=DEVICE_ENV)
    return result, sent


def expire(d, p):
    with d.store._connect() as con:
        con.execute('UPDATE human_sessions SET last_seen=?,expires_at=? WHERE id=?', (utc_text(utc_now()-timedelta(hours=9)), utc_text(utc_now()-timedelta(hours=1)), p.session_id))


def test_owner_staff_scope_timeout_generic_and_no_job_cross_delivery(dashboard):
    d=dashboard; p=d.credentials['principal']; _,wid,_,sid,_,manager,worker_raw,worker,other,_,supervisor=staff(d)
    for principal,surface,suffix in [(p,'owner','-owner'),(worker,'staff','-worker'),(other,'staff','-other'),(supervisor,'staff','-supervisor'),(manager,'staff','-manager')]:
        register(d,principal,surface,suffix)
    creation=create_task(wid,sid);tasks.append(d.store,manager,creation)
    assert len(push.feed(d.store,worker,'staff')['items'])==1
    assert push.feed(d.store,other,'staff')['items']==[]
    assert tasks.append(d.store,manager,creation)['status']=='ALREADY_RECORDED'
    expire(d,worker)
    assert request(d,'/api/auth/session',raw=worker_raw)[0]==401
    result,sent=deliver(d)
    assert result['sent']==4 and {pid for pid,_ in sent}=={p.id,worker.id,supervisor.id,manager.id}
    assert all(payload['body']=='Sign in to view your update.' and creation['title'] not in str(payload) for _,payload in sent)
    assert deliver(d)[0]['sent']==0
    assert web_push.dispatch_pending(d.store,sender=lambda *_:pytest.fail('Device subscriptions reached Job transport'),env=ENV)['sent']==0
    assert request(d,'/api/auth/session',raw=worker_raw)[0]==401


@pytest.mark.parametrize('change',['logout','disable','role','scope','assignment','unsubscribe','module','reassign'])
def test_revocation_checked_before_delivery(dashboard,change):
    d=dashboard;p=d.credentials['principal'];_,wid,other_id,_,_,manager,raw,worker,_,_,_=staff(d)
    register(d,worker,'staff');creation=create_task(wid);tasks.append(d.store,manager,creation)
    if change=='logout':
        expire(d,worker);assert request(d,'/api/auth/logout','POST',{},raw=raw)[0]==200
    elif change=='disable':IdentityService(d.store).disable_user(p,wid)
    elif change=='assignment':assign(d.store,p,wid,'SUPERVISOR')
    elif change=='unsubscribe':remove_push_subscription(d.store,wid,SUBSCRIPTION['endpoint'])
    elif change=='reassign':tasks.append(d.store,manager,event('REASSIGN',creation['task_id'],creation['event_id'],assigned_to=other_id,supervisor_id=None,reason='New assignee'))
    elif change=='module':
        from capabilities.contracts import Node,Mode
        controls=d.store._module_exposure.lifecycle.controls;node=Node('component',d.store._module_exposure.lifecycle.manifests.runtime_agent_id('farming'))
        controls.transition(controls.preview(node,Mode.DISABLED),actor='test',reason='test',confirmed=True)
    else:
        with d.store._connect() as con:
            if change=='role':con.execute("UPDATE human_identities SET role='Manager' WHERE id=?",(wid,))
            else:con.execute("UPDATE human_identities SET domains='[\"jobs\"]' WHERE id=?",(wid,))
    assert deliver(d)[0]['sent']==0


def test_api_csrf_role_denials_and_no_private_config(dashboard,monkeypatch):
    d=dashboard;_,_,_,_,_,manager,raw,worker,_,_,_=staff(d)
    for key,value in DEVICE_ENV.items():monkeypatch.setenv(key,value)
    path='/api/device-notifications/staff/'
    assert request(d,path+'status',raw='bad')[0]==401
    assert request(d,'/api/device-notifications/owner/feed',raw=raw)[0]==403
    assert request(d,path+'subscription','POST',SUBSCRIPTION,raw=raw,csrf=False)[0]==403
    assert request(d,path+'subscription','POST',{**SUBSCRIPTION,'after_timeout':True},raw=raw)[0]==200
    status,_,body=request(d,path+'status',raw=raw)
    assert status==200 and body['configured'] and 'development-private-key' not in str(body) and SUBSCRIPTION['endpoint'] not in str(body)
    with pytest.raises(PermissionError):register(d,manager,'owner')
    with pytest.raises(PermissionError):register(d,d.credentials['principal'],'staff')


def test_retry_and_reconnect_preserve_cursor_and_new_device_requires_consent(dashboard):
    d=dashboard;p=d.credentials['principal'];register(d,p,'owner')
    d.store.add_notification('Private Job content','Private application','URGENT',domain='jobs')
    before=push_subscriptions(d.store,p.id)[0]['cursor']
    def fail(*_):raise OSError('synthetic private failure')
    assert deliver(d,fail)[0]['failed']==1
    assert push_subscriptions(d.store,p.id)[0]['cursor']==before
    assert push.register(d.store,p,'owner',SUBSCRIPTION,DEVICE_ENV)['after_timeout']
    assert not push.register(d.store,p,'owner',{**SUBSCRIPTION,'endpoint':SUBSCRIPTION['endpoint']+'-new'},DEVICE_ENV)['after_timeout']
    assert deliver(d)[0]['sent']==1


def test_incident_alerts_are_atomic_and_assignment_scoped(dashboard):
    d=dashboard;_,_,_,_,_,_,_,worker,other,_,_=staff(d)
    register(d,worker,'staff');register(d,other,'staff','-other')
    payload=incident();incidents.append(d.store,worker,payload)
    assert incidents.append(d.store,worker,payload)['status']=='ALREADY_RECORDED'
    assert deliver(d)[0]['sent']==1
    assert push.feed(d.store,other,'staff')['items']==[]


def test_declined_consent_does_not_survive_expiry(dashboard):
    d=dashboard;p=d.credentials['principal'];register(d,p,'owner',consent=False)
    d.store.add_notification('Attention','Record','URGENT',domain='jobs')
    assert deliver(d)[0]['sent']==0
    expire(d,p)
    assert deliver(d)[0]['sent']==0


def test_task_notification_failure_rolls_back_work_and_event(dashboard,monkeypatch):
    from notifications.bus import NotificationBus
    d=dashboard;_,wid,_,_,_,manager,_,_,_,_,_=staff(d)
    with d.store._connect() as con: before=con.execute('SELECT COUNT(*) FROM domain_records').fetchone()[0]
    def fail(*args,**kwargs):raise ValueError('Synthetic notification failure')
    monkeypatch.setattr(NotificationBus,'publish',fail)
    with pytest.raises(ValueError):tasks.append(d.store,manager,create_task(wid))
    with d.store._connect() as con:assert con.execute('SELECT COUNT(*) FROM domain_records').fetchone()[0]==before


def test_account_switch_and_production_guard_fail_closed(dashboard,monkeypatch):
    from tests.checkpoint_f_fixture import PASSWORD
    d=dashboard;p=d.credentials['principal'];_,_,_,_,_,manager,raw,worker,_,_,_=staff(d)
    register(d,worker,'staff');expire(d,worker)
    assert request(d,'/api/auth/login','POST',{'username':'fixture-owner','password':PASSWORD},raw=raw)[0]==200
    assert push.register(d.store,p,'owner',{**SUBSCRIPTION,'endpoint':SUBSCRIPTION['endpoint']+'-owner'},DEVICE_ENV)['after_timeout'] is False
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','production')
    monkeypatch.setenv('CHIEF_SERVICE_CONFIGURED','1')
    monkeypatch.setenv('CHIEF_FARM_PRODUCTION','DISABLED')
    with pytest.raises(PermissionError,match='Farm production'):push.access(d.store,manager.id,manager.session_id,'staff',expired=True)
