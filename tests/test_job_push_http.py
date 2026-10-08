from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_identity_http import request


SUBSCRIPTION={
    'endpoint':'https://web.push.apple.com/Q-chief-http-test',
    'expirationTime':None,
    'keys':{'p256dh':'A'*87,'auth':'B'*22},
}


def configure_push(monkeypatch):
    monkeypatch.setenv('WEB_PUSH_VAPID_PUBLIC_KEY','C'*87)
    monkeypatch.setenv('WEB_PUSH_VAPID_PRIVATE_KEY','development-private-key')
    monkeypatch.setenv('WEB_PUSH_VAPID_SUBJECT','mailto:owner@example.invalid')


def login_as(dashboard, username, role, domains):
    service=IdentityService(dashboard.store)
    service.create_user(dashboard.credentials['principal'],username,PASSWORD,role,domains)
    raw,_=service.login(username,PASSWORD)
    return raw


def test_job_push_registration_requires_owner_admin_and_jobs_scope(dashboard,monkeypatch):
    configure_push(monkeypatch)
    d=dashboard

    # The fixture owner has global authority and may register the optional companion.
    assert request(d,'/api/notifications/push-subscription','POST',SUBSCRIPTION,d.credentials['raw'])[0]==200

    manager=login_as(d,'push-manager','Manager',('jobs',))
    assert request(d,'/api/notifications/push-subscription','POST',{**SUBSCRIPTION,'endpoint':'https://web.push.apple.com/Q-manager'},manager)[0]==403

    farm_owner=login_as(d,'push-farm-owner','Owner',('farming',))
    assert request(d,'/api/notifications/push-subscription','POST',{**SUBSCRIPTION,'endpoint':'https://web.push.apple.com/Q-farm-owner'},farm_owner)[0]==403

    administrator=login_as(d,'push-admin','Administrator',('*',))
    assert request(d,'/api/notifications/push-subscription','POST',{**SUBSCRIPTION,'endpoint':'https://web.push.apple.com/Q-admin'},administrator)[0]==200


def test_job_push_mutation_keeps_existing_csrf_and_origin_boundary(dashboard,monkeypatch):
    configure_push(monkeypatch)
    d=dashboard;raw=d.credentials['raw']
    assert request(d,'/api/notifications/push-subscription','POST',SUBSCRIPTION,raw,csrf=False)[0]==403
    assert request(d,'/api/notifications/push-subscription','POST',SUBSCRIPTION,raw,origin=False)[0]==403
    assert request(d,'/api/notifications/push-subscription','POST',SUBSCRIPTION,raw,extra={'Origin':'https://attacker.invalid'})[0]==403


def test_job_push_status_and_private_material_are_scope_safe(dashboard,monkeypatch):
    configure_push(monkeypatch)
    d=dashboard;raw=d.credentials['raw']
    assert request(d,'/api/notifications/push-subscription','POST',SUBSCRIPTION,raw)[0]==200

    status,_,preferences=request(d,'/api/notification-preferences',raw=raw)
    assert status==200
    assert preferences['job_push']['configured'] is True
    assert preferences['job_push']['subscribed'] is True
    serialized=str(preferences)
    assert SUBSCRIPTION['endpoint'] not in serialized
    assert SUBSCRIPTION['keys']['p256dh'] not in serialized
    assert SUBSCRIPTION['keys']['auth'] not in serialized

    farm_owner=login_as(d,'push-status-farm-owner','Owner',('farming',))
    status,_,preferences=request(d,'/api/notification-preferences',raw=farm_owner)
    assert status==200
    assert 'job_push' not in preferences


def test_job_push_unsubscribe_is_identity_scoped(dashboard,monkeypatch):
    configure_push(monkeypatch)
    d=dashboard;owner=d.credentials['raw']
    assert request(d,'/api/notifications/push-subscription','POST',SUBSCRIPTION,owner)[0]==200

    administrator=login_as(d,'push-other-admin','Administrator',('*',))
    status,_,result=request(d,'/api/notifications/push-unsubscribe','POST',{'endpoint':SUBSCRIPTION['endpoint']},administrator)
    assert status==200 and result['status']=='NOT_REGISTERED'

    status,_,result=request(d,'/api/notifications/push-unsubscribe','POST',{'endpoint':SUBSCRIPTION['endpoint']},owner)
    assert status==200 and result['status']=='REMOVED'
