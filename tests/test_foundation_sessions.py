from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from tests.checkpoint_f_fixture import e_base, f_fixture, PASSWORD
from tests.test_identity_http import request
from identity.service import IdentityService
from identity.contracts import AuthenticationRequired
from operations.time_integrity import utc_now, utc_text


def row(f):
    with f.store._connect() as con:
        return dict(con.execute('SELECT * FROM human_sessions WHERE id=?',(f.owner.session_id,)).fetchone())


def test_polling_does_not_extend_idle_and_expired_activity_cannot_revive(f_fixture):
    f=f_fixture;before=row(f);now=utc_now()
    service=IdentityService(f.store,now=lambda:now)
    for minute in (1,5,9):
        now=utc_now()+timedelta(minutes=minute)
        service.authenticate(f.raw)
    assert row(f)['last_seen']==before['last_seen']
    now=utc_now()+timedelta(minutes=10)
    with pytest.raises(AuthenticationRequired):service.authenticate(f.raw)
    with pytest.raises(AuthenticationRequired):service.activity(f.raw)
    assert row(f)['last_seen']==before['last_seen']


def test_activity_renews_idle_but_not_absolute_limit(f_fixture):
    f=f_fixture;before=row(f);now=utc_now();service=IdentityService(f.store,now=lambda:now)
    for _ in range(53):
        now+=timedelta(minutes=9);service.activity(f.raw)
    assert row(f)['expires_at']==before['expires_at']
    now+=timedelta(minutes=4)
    with pytest.raises(AuthenticationRequired):service.activity(f.raw)


def test_rotation_replaces_secret_not_approval_identity_or_absolute_expiry(f_fixture):
    f=f_fixture;before=row(f);action=f.store.add_action('Synthetic','fill_application_form')
    f.identity.approve_action(f.owner,action,'jobs')
    raw,principal=f.identity.reauthenticate(f.owner,PASSWORD,raw=f.raw)
    assert raw!=f.raw and f.identity.csrf(raw)!=f.identity.csrf(f.raw)
    assert principal.session_id==f.owner.session_id
    assert row(f)['expires_at']==before['expires_at']
    assert f.identity.approval_valid(action,'jobs')
    with pytest.raises(AuthenticationRequired):f.identity.authenticate(f.raw)
    assert f.identity.authenticate(raw).id==f.owner.id
    with f.store._connect() as con:assert con.execute('SELECT COUNT(*) FROM human_sessions').fetchone()[0]==1


def test_failed_reauth_does_not_change_session(f_fixture):
    f=f_fixture;before=row(f)
    with pytest.raises(AuthenticationRequired):f.identity.reauthenticate(f.owner,'Incorrect-fixture-password',raw=f.raw)
    assert row(f)==before


def test_two_concurrent_rotations_only_one_succeeds(f_fixture):
    f=f_fixture;barrier=Barrier(2)
    def rotate():
        barrier.wait()
        try:return f.identity.reauthenticate(f.owner,PASSWORD,raw=f.raw)[0]
        except AuthenticationRequired:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:rotate(),range(2)))
    assert sum(value is not None for value in results)==1
    f.identity.authenticate(next(value for value in results if value))
    with pytest.raises(AuthenticationRequired):f.identity.authenticate(f.raw)


def test_activity_http_requires_csrf_and_ignores_no_client_clock(dashboard):
    d=dashboard;raw=d.credentials['raw']
    assert request(d,'/api/auth/activity','POST',{},raw,csrf=False)[0]==403
    assert request(d,'/api/auth/activity','POST',{},raw,origin=False)[0]==403
    assert request(d,'/api/auth/activity','POST',{'timestamp':'2099-01-01T00:00:00Z'},raw)[0]==400
    assert request(d,'/api/auth/activity','POST',{},raw)[0]==200


def test_expired_http_mutation_cannot_change_state(dashboard):
    d=dashboard;raw=d.credentials['raw']
    with d.store._connect() as con:con.execute('UPDATE human_sessions SET last_seen=?',(utc_text(utc_now()-timedelta(minutes=11)),))
    assert request(d,'/api/model-controls','POST',{'model':'jobs.qwen','state':'DISABLED'},raw)[0]==401
    assert request(d,'/api/auth/activity','POST',{},raw)[0]==401
    from application.auth_routes import model_registry
    assert model_registry(d.store).state('jobs.qwen').value!='DISABLED'


def test_old_csrf_fails_with_rotated_cookie(dashboard):
    d=dashboard;old=d.credentials['raw']
    code,headers,_=request(d,'/api/auth/reauthenticate','POST',{'password':PASSWORD},old)
    assert code==200
    raw=headers['Set-Cookie'].split(';')[0].split('=',1)[1]
    assert request(d,'/api/auth/activity','POST',{},raw,extra={'X-Chief-CSRF':IdentityService.csrf(old)})[0]==403
    assert request(d,'/api/auth/activity','POST',{},raw)[0]==200
