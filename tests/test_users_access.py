import json
import os
from pathlib import Path
from datetime import timedelta
import pytest
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_identity_http import request
from operations.time_integrity import utc_now, utc_text


def capture_users_page(page, name):
    destination = os.environ.get('DASHBOARD_EVIDENCE_DIR')
    if destination:
        assert page.locator('#newUserPassword').input_value() == ''
        assert page.locator('#newUserPasswordRepeat').input_value() == ''
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        page.evaluate('window.scrollTo(0, 0)')
        page.screenshot(path=str(folder / (name + '.png')), full_page=True)


@pytest.mark.parametrize('role,scope,expected', [('Owner', ('*',), 200), ('Owner', ('jobs',), 403),
    ('Administrator', ('*',), 200), ('Manager', ('jobs',), 200), ('Worker', ('jobs',), 403)])
def test_users_page_and_api_permission_matrix(dashboard, role, scope, expected):
    d=dashboard; service=IdentityService(d.store)
    service.create_user(d.credentials['principal'], 'operator', PASSWORD, role, scope)
    raw, principal=service.login('operator', PASSWORD)
    context=request(d, '/api/ui/context', raw=raw)[2]
    assert ('users' in context['pages']) == (expected == 200)
    assert request(d, '/api/auth/users', raw=raw)[0] == expected
    assert request(d, '/api/auth/users', 'HEAD', raw=raw)[0] == expected
    body={'username':'newworker','password':PASSWORD,'role':'Worker','domains':['jobs']}
    assert request(d, '/api/auth/users', 'POST', body, raw)[0] == expected
    if expected != 200:
        with pytest.raises(PermissionError): service.create_user(principal, 'direct', PASSWORD, 'Worker', ['jobs'])
        with pytest.raises(PermissionError): service.disable_user(principal, d.credentials['principal'].id)


def test_owner_create_list_disable_preserves_history_and_hides_secrets(dashboard):
    d=dashboard; raw=d.credentials['raw']
    body={'username':'newworker','password':PASSWORD,'role':'Worker','domains':['jobs']}
    status, _, created=request(d, '/api/auth/users', 'POST', body, raw)
    assert status == 200
    service=IdentityService(d.store); user_raw, _=service.login('newworker', PASSWORD)
    listing=request(d, '/api/auth/users', raw=raw)[2]
    assert all(set(row)=={'id','username','role','domains','enabled','created_at','is_current_user'} for row in listing['users'])
    assert PASSWORD not in json.dumps(listing) and user_raw not in json.dumps(listing)
    assert request(d, '/api/auth/users/'+created['id']+'/disable', 'POST', {}, raw)[0] == 200
    assert request(d, '/api/auth/session', raw=user_raw)[0] == 401
    after=request(d, '/api/auth/users', raw=raw)[2]
    assert not next(row for row in after['users'] if row['id']==created['id'])['enabled']
    with d.store._connect() as con:
        events=[dict(row) for row in con.execute('SELECT * FROM human_security_events')]
        assert PASSWORD not in json.dumps(events) and user_raw not in json.dumps(events)
        assert any(row['operation']=='IDENTITY_CREATED' and row['human_id']==d.credentials['principal'].id for row in events)
        assert any(row['operation']=='IDENTITY_DISABLED' for row in events)


def test_users_require_authentication_csrf_origin_and_recent_reauthentication(dashboard):
    d=dashboard; raw=d.credentials['raw']
    body={'username':'newworker','password':PASSWORD,'role':'Worker','domains':['jobs']}
    assert request(d, '/api/auth/users')[0] == 401
    for change in ({'csrf':False}, {'origin':False}, {'extra':{'Host':'attacker.invalid'}}, {'extra':{'Origin':'https://attacker.invalid'}}):
        assert request(d, '/api/auth/users', 'POST', body, raw, **change)[0] == 403
    with d.store._connect() as con:
        con.execute('UPDATE human_sessions SET reauth_at=? WHERE id=?', (utc_text(utc_now()-timedelta(minutes=6)), d.credentials['principal'].session_id))
    assert request(d, '/api/auth/users', raw=raw)[0] == 200
    assert request(d, '/api/auth/users', 'POST', body, raw)[0] == 428
    with d.store._connect() as con:
        assert con.execute('SELECT count(*) FROM human_identities').fetchone()[0] == 1


def test_last_owner_and_unknown_scope_fail_closed(dashboard):
    d=dashboard; raw=d.credentials['raw']
    assert request(d, '/api/auth/users/'+d.credentials['principal'].id+'/disable', 'POST', {}, raw)[0] == 403
    body={'username':'unknown-scope','password':PASSWORD,'role':'Worker','domains':['unregistered']}
    assert request(d, '/api/auth/users', 'POST', body, raw)[0] == 400
    assert request(d, '/api/auth/session', raw=raw)[0] == 200


def test_owner_user_management_browser(dashboard):
    from playwright.sync_api import sync_playwright, expect
    d=dashboard
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page()
            page.goto(d.url)
            page.locator('#navLinks button[data-target="users"]').click()
            expect(page.locator('#users')).to_be_visible()
            expect(page.locator('#usersList')).to_contain_text('fixture-owner')
            expect(page.locator('#usersList button')).to_be_disabled()
            page.locator('#newUserName').fill('screen-worker')
            page.locator('#newUserDomains input[value="jobs"]').check()
            page.locator('#newUserPassword').fill(PASSWORD)
            page.locator('#newUserPasswordRepeat').fill(PASSWORD)
            page.locator('button[data-action="user-create"]').click()
            expect(page.locator('#usersList')).to_contain_text('screen-worker')
            expect(page.locator('#newUserPassword')).to_have_value('')
            expect(page.locator('#newUserPasswordRepeat')).to_have_value('')
            page.once('dialog', lambda dialog: dialog.accept())
            page.locator('#usersList button[data-username="screen-worker"]').click()
            expect(page.locator('#usersResult')).to_contain_text('Access disabled')
            expect(page.locator('#usersList button[data-username="screen-worker"]')).to_have_count(0)
            capture_users_page(page, 'users-owner')
        finally:
            browser.close()


@pytest.mark.parametrize('role', ['Worker'])
def test_worker_cannot_open_users_in_browser(dashboard, role):
    from playwright.sync_api import sync_playwright, expect
    d=dashboard; service=IdentityService(d.store)
    service.create_user(d.credentials['principal'], 'operator', PASSWORD, role, ['*'] if role=='Administrator' else ['jobs'])
    raw, _=service.login('operator', PASSWORD)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page()
            page.context.add_cookies([{'name':'chief_session','value':raw,'url':d.url,'httpOnly':True,'sameSite':'Strict'}])
            page.goto(d.url)
            page.wait_for_function('() => chiefContext !== null')
            assert page.url.endswith('/work')
            assert page.locator('#chiefAgents').count() == 0
            assert page.get_by_role('button', name='Manage Workers', exact=True).count() == 0
            assert page.evaluate("async()=>{try{await showWork('users');return 'opened';}catch{return 'denied';}}") == 'denied'
            expect(page.locator('#users')).to_be_hidden()
            assert page.evaluate("async()=> (await fetch('/api/auth/users')).status") == 403
            if role in ('Manager','Worker'):
                for target in ('settings','models','runtime','audit','components'):
                    assert page.locator('#navLinks button[data-target="'+target+'"]').count() == 0
        finally:
            browser.close()


@pytest.mark.parametrize('role', ['Administrator','Manager'])
def test_scoped_management_never_reaches_other_domains_or_privileged_roles(dashboard, role):
    d=dashboard; service=IdentityService(d.store); owner=d.credentials['principal']
    service.create_user(owner, 'supervisor', PASSWORD, role, ['jobs'])
    raw, principal=service.login('supervisor', PASSWORD)
    allowed=service.create_user(owner, 'local-worker', PASSWORD, 'Worker', ['jobs'])
    outside=service.create_user(owner, 'outside-worker', PASSWORD, 'Worker', ['farming'])
    mixed=service.create_user(owner, 'shared-worker', PASSWORD, 'Worker', ['jobs','farming'])
    listing=request(d, '/api/auth/users', raw=raw)[2]
    names={r['username'] for r in listing['users']}
    assert 'local-worker' in names
    assert not {'fixture-owner','supervisor','outside-worker','shared-worker'} & names
    for target in (outside,mixed,owner.id,principal.id):
        assert request(d, '/api/auth/users/'+target+'/disable', 'POST', {}, raw)[0] == 403
    for new_role, domains in [('Owner',['jobs']),('Administrator',['jobs']),('Worker',['farming']),('Worker',['jobs','farming']),('Worker',['*'])]:
        assert request(d, '/api/auth/users', 'POST', {'username':'forbidden','password':PASSWORD,'role':new_role,'domains':domains}, raw)[0] == 403
    if role=='Manager':
        assert request(d, '/api/auth/users', 'POST', {'username':'peer','password':PASSWORD,'role':'Manager','domains':['jobs']}, raw)[0] == 403
    assert request(d, '/api/auth/users/'+allowed+'/disable', 'POST', {}, raw)[0] == 200
    with d.store._connect() as con:
        assert con.execute('SELECT enabled FROM human_identities WHERE id=?',(outside,)).fetchone()[0] == 1
        assert con.execute('SELECT enabled FROM human_identities WHERE id=?',(mixed,)).fetchone()[0] == 1


def test_manager_browser_exposes_only_scoped_worker_management(dashboard):
    from playwright.sync_api import sync_playwright, expect
    d=dashboard; service=IdentityService(d.store)
    service.create_user(d.credentials['principal'], 'manager', PASSWORD, 'Manager', ['jobs'])
    raw, _=service.login('manager', PASSWORD)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page()
            page.context.add_cookies([{'name':'chief_session','value':raw,'url':d.url,'httpOnly':True,'sameSite':'Strict'}])
            page.goto(d.url)
            assert page.url.endswith('/work')
            page.get_by_role('button', name='Manage Workers', exact=True).click()
            expect(page.locator('#usersScopeNotice')).to_contain_text('Workers entirely within')
            expect(page.locator('#newUserRole option')).to_have_text(['Worker'])
            expect(page.locator('#userGlobalLabel')).to_be_hidden()
            expect(page.locator('#newUserDomains input')).to_have_count(1)
            assert page.locator('#usersList').inner_text().find('fixture-owner') == -1
            for target in ('settings','models','runtime','audit','components'):
                assert page.locator('#navLinks button[data-target="'+target+'"]').count() == 0
            page.locator('#newUserName').fill('managed-worker')
            page.locator('#newUserDomains input[value="jobs"]').check()
            page.locator('#newUserPassword').fill(PASSWORD)
            page.locator('#newUserPasswordRepeat').fill(PASSWORD)
            page.locator('button[data-action="user-create"]').click()
            expect(page.locator('#usersList')).to_contain_text('managed-worker')
            expect(page.locator('#newUserPassword')).to_have_value('')
            capture_users_page(page, 'users-manager')
        finally:browser.close()


def test_manager_mutations_require_recent_reauthentication_and_live_authority(dashboard):
    d = dashboard
    service = IdentityService(d.store)
    owner = d.credentials['principal']
    manager = service.create_user(owner, 'scoped-manager', PASSWORD, 'Manager', ['jobs'])
    worker = service.create_user(owner, 'scoped-worker', PASSWORD, 'Worker', ['jobs'])
    raw, principal = service.login('scoped-manager', PASSWORD)
    body = {'username': 'another-worker', 'password': PASSWORD, 'role': 'Worker', 'domains': ['jobs']}
    with d.store._connect() as con:
        con.execute('UPDATE human_sessions SET reauth_at=? WHERE id=?',
                    (utc_text(utc_now() - timedelta(minutes=6)), principal.session_id))
    assert request(d, '/api/auth/users', raw=raw)[0] == 200
    assert request(d, '/api/auth/users', 'POST', body, raw)[0] == 428
    assert request(d, '/api/auth/users/' + worker + '/disable', 'POST', {}, raw)[0] == 428
    with d.store._connect() as con:
        assert con.execute('SELECT enabled FROM human_identities WHERE id=?', (worker,)).fetchone()[0] == 1
        assert not con.execute('SELECT 1 FROM human_identities WHERE username=?', ('another-worker',)).fetchone()
    service.disable_user(owner, manager)
    assert request(d, '/api/auth/users', raw=raw)[0] == 401
    with pytest.raises(PermissionError):
        service.create_user(principal, 'stale-authority', PASSWORD, 'Worker', ['jobs'])


def test_domain_owner_cannot_replace_last_installation_owner(dashboard):
    d = dashboard
    service = IdentityService(d.store)
    owner = d.credentials['principal']
    service.create_user(owner, 'domain-owner', PASSWORD, 'Owner', ['jobs'])
    assert request(d, '/api/auth/users/' + owner.id + '/disable', 'POST', {}, d.credentials['raw'])[0] == 403
    assert request(d, '/api/auth/session', raw=d.credentials['raw'])[0] == 200
    service.create_user(owner, 'successor-owner', PASSWORD, 'Owner', ['*'])
    service.disable_user(owner, owner.id)
    _, successor = service.login('successor-owner', PASSWORD)
    assert service.user_management(successor)['roles']
    with pytest.raises(PermissionError):
        service.disable_user(successor, successor.id)


def test_owner_retains_extensible_domain_role_management(dashboard):
    d = dashboard
    service = IdentityService(d.store, extra_roles={'Analyst': {'work.read'}})
    identity = service.create_user(d.credentials['principal'], 'analyst', PASSWORD, 'Analyst', ['jobs'])
    _, analyst = service.login('analyst', PASSWORD)
    assert identity in {row['id'] for row in service.list_users(d.credentials['principal'])}
    with pytest.raises(PermissionError):
        service.user_management(analyst)
    service.disable_user(d.credentials['principal'], identity)
