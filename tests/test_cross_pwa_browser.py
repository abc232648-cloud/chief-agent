"""Browser session/cache behavior at the shared Chief API origin.

These are browser HTTP-boundary tests, not tests of externally built PWA bundles.
"""
from playwright.sync_api import sync_playwright

from application.exposure import for_store
from capabilities.contracts import Mode, Node
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD


def fetch(page, path):
    return page.evaluate('''async path => {
        const response = await fetch(path);
        return {status: response.status, cache: response.headers.get('Cache-Control'),
                body: await response.json()};
    }''', path)


def test_live_browser_sessions_do_not_turn_cached_discovery_into_authority(dashboard):
    d = dashboard
    service = IdentityService(d.store)
    service.create_user(d.credentials['principal'], 'browser-staff', PASSWORD, 'Worker', ('farming',))
    staff_raw, _ = service.login('browser-staff', PASSWORD)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            owner_context = browser.new_context()
            staff_context = browser.new_context()
            staff_context.add_cookies([{'name': 'chief_session', 'value': staff_raw,
                                       'url': d.url, 'httpOnly': True, 'sameSite': 'Strict'}])
            owner_page = owner_context.new_page()
            companion_page = owner_context.new_page()
            staff_page = staff_context.new_page()
            for page in (owner_page, companion_page, staff_page):
                page.goto(d.url + '/login')
            assert fetch(owner_page, '/api/ui/evidence/jobs')['status'] == 200
            assert fetch(companion_page, '/api/ui/job-feed')['status'] == 200
            cached = fetch(staff_page, '/api/ui/context')
            assert cached['status'] == 200 and cached['cache'] == 'no-store'
            assert cached['body']['interfaces'][0]['kind'] == 'staff'
            staff_page.evaluate('value => localStorage.setItem("cached-discovery", JSON.stringify(value))', cached['body'])
            assert fetch(staff_page, '/api/ui/job-feed')['status'] == 403
            gate = for_store(d.store)
            node = Node('component', gate.lifecycle.manifests.runtime_agent_id('farming'))
            controls = gate.lifecycle.controls
            controls.transition(controls.preview(node, Mode.DISABLED), actor='a10-browser', reason='Revoke live access', confirmed=True)
            denied = fetch(staff_page, '/api/farm/tasks')
            assert denied['status'] == 403 and denied['cache'] == 'no-store'
            assert fetch(staff_page, '/api/ui/context')['body']['interfaces'] == []
            assert staff_page.evaluate('JSON.parse(localStorage.getItem("cached-discovery")).interfaces.length') == 1
            controls.transition(controls.preview(node, Mode.ENABLED), actor='a10-browser', reason='Restore live access', confirmed=True)
            assert fetch(staff_page, '/api/farm/tasks')['status'] == 200
            service.revoke(d.credentials['principal'])
            assert fetch(owner_page, '/api/ui/evidence/jobs')['status'] == 401
            assert fetch(companion_page, '/api/ui/job-feed')['status'] == 401
            assert fetch(staff_page, '/api/farm/tasks')['status'] == 200
        finally:
            browser.close()
