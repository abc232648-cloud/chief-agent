from dataclasses import replace
from datetime import timedelta
import json,sqlite3
import pytest
from tests.checkpoint_f_fixture import e_base,f_fixture,PASSWORD
from identity.service import IdentityService
from identity.contracts import MATRIX,roles,AuthenticationRequired,ReauthenticationRequired
from operations.time_integrity import utc_now,utc_text

def user(f,role,domains=('jobs',),name=None):
    name=name or role.lower();identity=f.identity.create_user(f.owner,name,PASSWORD,role,domains)
    raw,principal=f.identity.login(name,PASSWORD);return identity,raw,principal

@pytest.mark.parametrize('role',['Owner','Administrator','Manager','Worker'])
def test_permission_matrix(f_fixture,role):
    f=f_fixture;principal=f.owner if role=='Owner' else user(f,role,('*',) if role=='Administrator' else ('jobs',))[2]
    permissions=set().union(*MATRIX.values())|{'runtime.manage','capability.grant','safety.pause'}
    for permission in permissions:
        domain='jobs' if permission.startswith('work.') or permission in {'safety.pause','identity.workers.manage'} else None
        expected=permission in MATRIX[role] or (permission=='safety.pause' and role in {'Owner','Administrator'})
        if expected:assert f.identity.authorize(principal,permission,domain,'action:1')=='ROLE'
        else:
            with pytest.raises(PermissionError):f.identity.authorize(principal,permission,domain,'action:1')

def test_domain_scope_and_extensible_roles(f_fixture):
    f=f_fixture;principal=user(f,'Manager')[2]
    f.identity.authorize(principal,'work.read','jobs')
    for domain in ('farming','other'):
        with pytest.raises(PermissionError):f.identity.authorize(principal,'work.read',domain)
    assert roles({'Analyst':{'work.read'}})['Analyst']==frozenset({'work.read'})
    with pytest.raises(ValueError):roles({'Analyst':{'installation.manage'}})
    with pytest.raises(ValueError):roles({'Owner':{'work.read'}})

@pytest.mark.parametrize('kind',['idle','absolute','revoked','disabled','future'])
def test_session_failure_closed(f_fixture,kind):
    f=f_fixture;identity,raw,principal=user(f,'Worker')
    now=utc_now();service=IdentityService(f.store,now=lambda:now)
    if kind=='idle':now+=timedelta(minutes=31)
    elif kind=='absolute':
        now+=timedelta(hours=9)
        with f.store._connect() as con:con.execute('UPDATE human_sessions SET last_seen=? WHERE id=?',(utc_text(now),principal.session_id))
    elif kind=='revoked':service.revoke(principal)
    elif kind=='disabled':service.disable_user(f.owner,identity)
    else:now-=timedelta(days=1)
    with pytest.raises(AuthenticationRequired):service.authenticate(raw)

def test_sensitive_reauth_and_wrong_password_throttle(f_fixture):
    f=f_fixture;now=utc_now()+timedelta(minutes=6);service=IdentityService(f.store,now=lambda:now)
    principal=service.authenticate(f.raw)
    with pytest.raises(ReauthenticationRequired):service.authorize(principal,'installation.manage')
    for _ in range(5):
        with pytest.raises(AuthenticationRequired):service.reauthenticate(principal,'Wrong-password-2026',raw=f.raw)
    with pytest.raises(AuthenticationRequired):service.reauthenticate(principal,PASSWORD,raw=f.raw)
    now+=timedelta(minutes=6)
    # The accepted ten-minute idle boundary has also elapsed. A password cannot
    # revive that old session; normal sign-in remains available after lockout.
    with pytest.raises(AuthenticationRequired):service.reauthenticate(principal,PASSWORD,raw=f.raw)
    _,principal=service.login('owner',PASSWORD)
    assert service.authorize(principal,'installation.manage')=='ROLE'

def test_owner_admin_boundaries_and_stale_principal(f_fixture):
    f=f_fixture;admin=user(f,'Administrator',('*',))[2]
    with pytest.raises(PermissionError):f.identity.create_user(admin,'new-owner',PASSWORD,'Owner',('*',))
    with pytest.raises(PermissionError):f.identity.disable_user(admin,f.owner_id)
    with pytest.raises(PermissionError):f.identity.disable_user(f.owner,f.owner_id)
    forged=replace(admin,role='Owner')
    with pytest.raises(PermissionError):f.identity.create_user(forged,'forged-owner',PASSWORD,'Owner',('*',))

def test_no_credentials_in_audit_and_only_hashes_persist(f_fixture):
    f=f_fixture;f.identity.event(f.owner,'SYNTHETIC_OPERATION','jobs','action:1')
    with f.store._connect() as con:
        events=json.dumps([dict(r) for r in con.execute('SELECT * FROM human_security_events')])
        sessions=json.dumps([dict(r) for r in con.execute('SELECT * FROM human_sessions')])
        identity=con.execute('SELECT password_hash FROM human_identities WHERE id=?',(f.owner.id,)).fetchone()[0]
        assert PASSWORD not in identity and ':' in identity
        for secret in (PASSWORD,f.raw,f.identity.csrf(f.raw)):
            assert secret not in events and secret not in sessions
        with pytest.raises(sqlite3.IntegrityError):con.execute('DELETE FROM human_security_events')

def test_bootstrap_is_explicit_once(f_fixture):
    with pytest.raises(PermissionError):f_fixture.identity.bootstrap('another-owner',PASSWORD)
