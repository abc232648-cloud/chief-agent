"""Actual built PWAs and real Chief HTTP, with disposable identities and TLS.

Opt in with CHIEF_A10_CLIENT_ROOT pointing at sibling client checkouts. Missing
bundles in an opted-in run fail, rather than silently substituting fixtures.
The TLS proxy is test-only; this does not qualify installed-host TLS.
"""
import os
import time
import base64
import hashlib
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright, expect

from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_http_transport import proxy_server
from tests.tls_proxy_fixture import local_tls_proxy
from capabilities.contracts import Mode, Node
from application.exposure import for_store


@pytest.fixture
def paired(dashboard, tmp_path):
    root = os.environ.get('CHIEF_A10_CLIENT_ROOT')
    if not root:
        pytest.skip('Paired PWA builds not supplied; set CHIEF_A10_CLIENT_ROOT')
    bundles = {f'/{prefix}/': Path(root) / repo / 'dist' for prefix, repo in (
        ('owner', 'chief-owner-pwa'), ('staff', 'chief-staff-pwa'), ('jobs', 'Job-agent-pwa'))}
    for directory in bundles.values():
        assert (directory / 'index.html').is_file(), f'Missing production build: {directory}'
    service = IdentityService(dashboard.store)
    service.create_user(dashboard.credentials['principal'], 'a10-worker', PASSWORD, 'Worker', ('farming',))
    dashboard.a10_faults = {}
    dashboard.a10_bundles = bundles
    with proxy_server(dashboard):
        with local_tls_proxy(tmp_path, urlsplit(dashboard.url).port, bundles=bundles, faults=dashboard.a10_faults) as (origin, _):
            with sync_playwright() as p:
                engine = os.environ.get('CHIEF_A10_BROWSER', 'chromium')
                assert engine in {'chromium', 'firefox', 'webkit'}
                dashboard.a10_browser_engine = engine
                launch = {'headless': True}
                if engine == 'chromium':
                    # Only trust this ephemeral loopback fixture certificate.
                    # Browser context HTTPS bypass alone does not cover SW fetches.
                    from cryptography import x509
                    from cryptography.hazmat.primitives import serialization
                    cert = x509.load_pem_x509_certificate((tmp_path/'ephemeral-tls-cert.pem').read_bytes())
                    spki = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
                    pin = base64.b64encode(hashlib.sha256(spki).digest()).decode()
                    launch['args'] = ['--ignore-certificate-errors-spki-list=' + pin]
                browser = None
                if engine == 'webkit':
                    # WebKit private contexts cannot persist Blob evidence in
                    # this runtime. Use a fresh, disposable installed-PWA-like
                    # profile; never a user's real browser profile.
                    context = p.webkit.launch_persistent_context(
                        str(tmp_path / 'webkit-profile'), **launch, ignore_https_errors=True)
                else:
                    browser = getattr(p, engine).launch(**launch)
                    context = browser.new_context(ignore_https_errors=True)
                context.clear_cookies()  # No inherited Owner fixture login.
                try:
                    yield dashboard, origin, context
                finally:
                    context.close()
                    if browser:
                        browser.close()


def set_network_unavailable(dashboard, context, unavailable):
    if dashboard.a10_browser_engine == 'webkit':
        # WebKit offline emulation also breaks local File reads and can leave
        # worker fetches offline after restoration. Interrupt real test-server
        # transport instead. navigator.onLine stays true: an unreachable origin
        # must still preserve evidence without inventing a successful receipt.
        dashboard.a10_faults['network_unavailable'] = unavailable
    else:
        context.set_offline(unavailable)


def wait_async(page, expression, *, arg=None, timeout=10000):
    """Await each async predicate result; a Promise itself is not a condition."""
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        if page.evaluate(expression, arg):
            return
        page.wait_for_timeout(100)
    raise AssertionError('Async browser condition did not become true: ' + expression)


def sign_in(context, origin, prefix, *, username=None, install_clock=True):
    page = context.new_page()
    if install_clock:
        page.clock.install()
    page.goto(origin + '/' + prefix + '/')
    page.locator('input[autocomplete="username"]').fill(username or ('a10-worker' if prefix == 'staff' else 'fixture-owner'))
    page.locator('input[type="password"]').fill(PASSWORD)
    with page.expect_response(lambda response: response.url.endswith('/api/auth/login')) as login:
        page.locator('button[type="submit"]').click()
    assert login.value.status == 200
    expect(page.locator('input[type="password"]')).to_have_count(0)
    result = page.evaluate("async()=>{const r=await fetch('/api/auth/session');return {status:r.status,body:await r.json()}}")
    assert result['status'] == 200
    if prefix == 'staff':
        expect(page.get_by_role('button', name='Sign out of Chief')).to_be_visible()
    return page, result['body']['principal']['session_id']


@pytest.mark.parametrize('prefix', ['owner', 'staff', 'jobs'])
def test_actual_bundle_login_and_api_cache_isolation(paired, prefix):
    d, origin, context = paired
    page, _ = sign_in(context, origin, prefix)
    assert page.evaluate("async()=> (await fetch('/api/ui/context')).status") == 200
    wait_async(page, "async prefix => (await navigator.serviceWorker.getRegistrations()).some(r => r.active && new URL(r.scope).pathname === '/' + prefix + '/')", arg=prefix)
    assert page.evaluate("async()=> (await navigator.serviceWorker.getRegistrations()).every(r=>['/owner/','/staff/','/jobs/'].includes(new URL(r.scope).pathname))")
    assert page.evaluate("async()=>{let urls=[];for(const k of await caches.keys()){for(const r of await (await caches.open(k)).keys())urls.push(r.url)}return urls.every(u=>!new URL(u).pathname.startsWith('/api/'))}")
    assert page.evaluate("()=>Object.keys(localStorage).every(k=>!/(password|csrf|session.token)/i.test(k))")


def test_owner_notification_drawer_does_not_invent_alerts(paired):
    d, origin, context = paired
    d.store.add_notification('Recorded test notice', 'Synthetic authorized notice.', 'URGENT', domain='jobs')
    page, _ = sign_in(context, origin, 'owner')
    page.get_by_role('button', name='Open notifications', exact=True).click()
    drawer = page.get_by_role('dialog', name='Notifications Drawer')
    expect(drawer).to_be_visible()
    expect(drawer).to_contain_text('Recorded test notice')
    expect(drawer).to_contain_text('Background delivery is not configured')
    for sample in ('Critical Interlock Triggered', 'Scheduled Telemetry Sync Complete', 'Live Socket Active'):
        expect(drawer).not_to_contain_text(sample)


@pytest.mark.parametrize('surface', ['owner', 'staff'])
def test_device_notification_controls_use_real_authorized_api(paired, monkeypatch, surface):
    from tests.test_device_notifications import DEVICE_ENV
    from control.notifications import push_subscriptions
    d, origin, context = paired
    for key, value in DEVICE_ENV.items(): monkeypatch.setenv(key, value)
    # Browser PushManager is simulated; HTTP identity, CSRF and persistence are real.
    context.add_init_script("""(() => {
      window.PushManager = window.PushManager || function(){};
      Object.defineProperty(window, 'Notification', {value:{permission:'granted',requestPermission:async()=> 'granted'}, configurable:true});
      let subscribed = false;
      const sub = {endpoint:'https://web.push.apple.com/test-device',toJSON(){return {endpoint:this.endpoint,keys:{p256dh:'A'.repeat(87),auth:'B'.repeat(22)}}},unsubscribe:async()=>{subscribed=false;return true}};
      navigator.serviceWorker.getRegistration = async path => ({scope:location.origin+path,active:{},pushManager:{getSubscription:async()=>subscribed?sub:null,subscribe:async()=>{subscribed=true;return sub}}});
    })();""")
    page, _ = sign_in(context, origin, surface)
    if surface == 'owner': page.get_by_role('button', name='Open notifications', exact=True).click()
    else: page.get_by_role('link', name='More', exact=True).click()
    panel=page.get_by_role('region', name='Device notifications')
    panel.get_by_role('button', name='Enable device alerts', exact=True).click()
    expect(panel.get_by_role('status')).to_contain_text('Device registered')
    rows=push_subscriptions(d.store)
    assert len(rows)==1 and rows[0]['surface']==surface and rows[0]['consent_version']==1
    panel.get_by_role('button', name='Disable device alerts', exact=True).click()
    expect(panel.get_by_role('status')).to_contain_text('disabled on this device')
    assert push_subscriptions(d.store)==[]


@pytest.mark.parametrize('prefix', ['owner', 'staff'])
def test_unattended_client_does_not_extend_idle_session(paired, prefix):
    d, origin, context = paired
    page, session_id = sign_in(context, origin, prefix)
    old = utc_text(utc_now() - timedelta(minutes=9))
    with d.store._connect() as con:
        con.execute('UPDATE human_sessions SET last_seen=? WHERE id=?', (old, session_id))
    # Advance browser timers, not the server clock. A timer must not renew last_seen.
    page.clock.run_for(301_000)
    page.wait_for_timeout(500)
    with d.store._connect() as con:
        assert con.execute('SELECT last_seen FROM human_sessions WHERE id=?', (session_id,)).fetchone()[0] == old


@pytest.mark.parametrize('prefix', ['owner', 'staff'])
def test_human_activity_and_expiry(paired, prefix):
    d, origin, context = paired
    page, session_id = sign_in(context, origin, prefix)
    old = utc_text(utc_now() - timedelta(minutes=9))
    with d.store._connect() as con:
        con.execute('UPDATE human_sessions SET last_seen=? WHERE id=?', (old, session_id))
    page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'a',bubbles:true}))")
    page.wait_for_timeout(100)
    with d.store._connect() as con:
        assert con.execute('SELECT last_seen FROM human_sessions WHERE id=?', (session_id,)).fetchone()[0] == old
    # Browser input is trusted; script-created events above are not.
    with page.expect_response(lambda r: r.url.endswith('/api/auth/activity')) as response:
        page.keyboard.press('Shift')
    assert response.value.status == 200
    with d.store._connect() as con:
        assert con.execute('SELECT last_seen FROM human_sessions WHERE id=?', (session_id,)).fetchone()[0] != old
        con.execute('UPDATE human_sessions SET last_seen=? WHERE id=?', (utc_text(utc_now() - timedelta(minutes=11)), session_id))
    page.clock.run_for(31_000)
    expect(page.locator('input[type="password"]')).to_be_visible()
    assert page.evaluate("async()=> (await fetch('/api/ui/context')).status") == 401


@pytest.mark.parametrize('prefix', ['owner', 'staff', 'jobs'])
def test_logout_in_other_tab_revokes_api_access(paired, prefix):
    d, origin, context = paired
    page, session_id = sign_in(context, origin, prefix)
    second = context.new_page()
    second.goto(origin + '/' + prefix + '/')
    result = second.evaluate("async()=>{const s=await (await fetch('/api/auth/session')).json();return (await fetch('/api/auth/logout',{method:'POST',headers:{'Content-Type':'application/json','X-Chief-CSRF':s.csrf},body:'{}'})).status}")
    assert result == 200
    page.clock.run_for(31_000)
    expect(page.locator('input[type="password"]')).to_be_visible()
    assert page.evaluate("async()=> (await fetch('/api/ui/context')).status") == 401


@pytest.mark.parametrize('prefix,domain,path', [
    ('owner', 'jobs', '/api/ui/job-feed'),
    ('jobs', 'jobs', '/api/ui/job-feed'),
    ('staff', 'farming', '/api/farm/setup'),
])
def test_lifecycle_denial_and_recovery_with_actual_client(paired, prefix, domain, path):
    d, origin, context = paired
    page, _ = sign_in(context, origin, prefix)
    gate = for_store(d.store)
    controls = gate.lifecycle.controls
    node = Node('component', gate.lifecycle.manifests.runtime_agent_id(domain))
    for mode in (Mode.DISABLED, Mode.MAINTENANCE, Mode.ENABLED):
        controls.transition(controls.preview(node, mode), actor='a10-fixture', reason='Synthetic acceptance', confirmed=True)
        status = page.evaluate('async path => (await fetch(path)).status', path)
        assert status == (200 if mode == Mode.ENABLED else 403)
    assert d.store.counts()['commands'] == 0


def test_staff_cannot_mutate_privileged_model_state(paired):
    d, origin, context = paired
    page, _ = sign_in(context, origin, 'staff')
    from application.auth_routes import model_registry
    before = model_registry(d.store).state('jobs.qwen')
    code = page.evaluate("async()=>{const s=await(await fetch('/api/auth/session')).json();return(await fetch('/api/model-controls',{method:'POST',headers:{'Content-Type':'application/json','X-Chief-CSRF':s.csrf},body:JSON.stringify({model:'jobs.qwen',state:'DISABLED',role:'Owner'})})).status}")
    assert code == 403
    assert model_registry(d.store).state('jobs.qwen') == before
    with d.store._connect() as con:
        assert con.execute("SELECT count(*) FROM human_security_events WHERE outcome='DENIED'").fetchone()[0] > 0


def test_job_surface_clears_previous_owners_data_after_account_switch(paired):
    d, origin, context = paired
    d.store.add_notification('A10 private owner notice', 'Synthetic private content', 'ACTION_REQUIRED', domain='jobs')
    page, _ = sign_in(context, origin, 'jobs')
    expect(page.get_by_text('A10 private owner notice').first).to_be_visible()
    # A second same-origin tab changes the shared HttpOnly cookie legitimately.
    second = context.new_page()
    second.goto(origin + '/staff/')
    assert second.evaluate("async password => (await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'a10-worker',password})})).status", PASSWORD) == 200
    page.clock.run_for(31_000)
    expect(page.get_by_text('A10 private owner notice')).to_have_count(0)
    assert page.evaluate("async()=> (await fetch('/api/ui/job-feed')).status") == 403


@pytest.mark.parametrize('interruption', ['offline', 'lost_response'])
def test_actual_staff_report_queue_reconciles_without_duplicate(paired, interruption):
    d, origin, context = paired
    page, _ = sign_in(context, origin, 'staff')
    wait_async(page, "async()=> (await navigator.serviceWorker.getRegistrations()).some(r=>r.active && new URL(r.scope).pathname==='/staff/')")
    page.goto(origin + '/staff/#/report')
    page.get_by_placeholder('e.g. House 1', exact=True).fill('Synthetic A10 house')
    page.get_by_placeholder('Describe only what you saw, counted, measured, or were told to record.').fill('A10 observed synthetic report')
    if interruption == 'offline':
        set_network_unavailable(d, context, True)
    else:
        d.a10_faults['drop_response'] = ('POST', '/api/farm/worker-reports')
    page.get_by_role('button', name='Send report to Chief', exact=True).click()
    expect(page.get_by_text('Stored on this device', exact=True)).to_be_visible()
    if interruption == 'lost_response':
        assert d.a10_faults['accepted_status'] == 200
        assert d.a10_faults['dropped_statuses']
        assert all(status == 200 for status in d.a10_faults['dropped_statuses'])
    # Close before reconnect, so the old tab cannot auto-sync on its online event.
    page.close()
    d.a10_faults.pop('drop_response', None)
    set_network_unavailable(d, context, False)
    # Reload proves recovery uses durable device storage, not a component variable.
    page = context.new_page()
    diagnostics = []
    page.on('pageerror', lambda error: diagnostics.append(str(error)))
    page.on('requestfailed', lambda req: diagnostics.append(urlsplit(req.url).path + ': ' + str(req.failure)))
    page.goto(origin + '/staff/#/more')
    try:
        expect(page.get_by_role('link', name='More', exact=True)).to_be_visible(timeout=5000)
    except AssertionError:
        raise AssertionError('Recovered page: ' + page.locator('body').inner_text()[:1800] + ' errors=' + str(diagnostics)) from None
    page.get_by_role('link', name='More', exact=True).click()
    send = page.get_by_role('button', name='Send queued to Chief', exact=True)
    expect(send).to_be_enabled()
    send.click()
    expect(page.get_by_text('No queued records for this authenticated identity.', exact=True)).to_be_visible()
    result = page.evaluate("async()=> (await fetch('/api/farm/worker-reports')).json()")
    matches = [r for r in result['reports'] if r['payload']['summary'] == 'A10 observed synthetic report']
    assert len(matches) == 1
    assert matches[0]['evidence_status'] == 'HUMAN_REPORTED'
    assert matches[0]['payload']['quantity'] is None


@pytest.mark.parametrize('prefix', ['owner', 'staff', 'jobs'])
@pytest.mark.parametrize('width,height', [(360, 800), (768, 1024), (1440, 900)])
def test_responsive_login_at_phone_tablet_and_desktop_sizes(paired, prefix, width, height):
    _, origin, context = paired
    page = context.new_page()
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(origin + '/' + prefix + '/')
    expect(page.locator('input[type="password"]')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.locator('input[autocomplete="username"]').fill('synthetic-check')
    page.locator('input[type="password"]').fill(PASSWORD)
    expect(page.locator('button[type="submit"]')).to_be_enabled()
