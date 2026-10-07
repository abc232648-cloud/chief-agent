"""Actual browser acceptance; a blocked launch remains a failure, never a pass."""
from playwright.sync_api import sync_playwright,expect
from tests.browser_navigation import navigate

def test_h_grouped_navigation_health_and_unqualified_runtime(dashboard):
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page();page.goto(dashboard.url)
        expect(page.locator('#navLinks')).to_contain_text('Work & Control')
        navigate(page,'health');expect(page.locator('#healthContent')).to_contain_text('UNKNOWN')
        navigate(page,'runtime');expect(page.locator('#runtimeContent')).to_contain_text('No runtime is qualified or selected')
        assert page.locator('#runtimeContent button').count()==0
        navigate(page,'devices');expect(page.locator('#devicesContent')).to_contain_text('Device connections are not available yet')
        assert page.locator('#devicesContent button').count()==0
        page.set_viewport_size({'width':390,'height':844})
        navigate(page,'models');expect(page.locator('#modelsContent')).to_contain_text('OPEN')
        expect(page.locator('#modelsContent')).to_contain_text('Latest provider observations')
        expect(page.locator('#modelsContent')).to_contain_text('not a live connection check')
        browser.close()


def test_h_worker_sees_only_scoped_workspace(dashboard):
    from identity.service import IdentityService
    from tests.checkpoint_f_fixture import PASSWORD
    service=IdentityService(dashboard.store)
    service.create_user(dashboard.credentials['principal'],'browser-worker',PASSWORD,'Worker',('jobs',))
    raw,_=service.login('browser-worker',PASSWORD)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);context=browser.new_context()
        context.clear_cookies();context.add_cookies([{'name':'chief_session','value':raw,'url':dashboard.url,'httpOnly':True,'sameSite':'Strict'}])
        page=context.new_page();bad=[]
        page.on('response',lambda response:bad.append(response.status) if response.status>=400 else None)
        page.goto(dashboard.url)
        expect(page).to_have_url(dashboard.url+'/work')
        expect(page.locator('#workNavigation')).to_contain_text('Job work')
        expect(page.locator('#workNavigation')).not_to_contain_text('Farm work')
        assert page.locator('#chiefAgents,#models').count()==0
        expect(page.locator('#jobWork')).to_be_visible()
        assert not bad,bad
        context.close();browser.close()


def test_late_navigation_cannot_replace_newer_agent_selection(dashboard):
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page();page.goto(dashboard.url)
        expect(page.locator('#chiefAgents')).to_contain_text('Farm Agent')
        held=[]
        def delay_first_context(route):
            if not held:held.append(route)
            else:route.continue_()
        page.route('**/api/ui/context',delay_first_context)
        page.evaluate("()=>{window.oldNavigation=show('domains');}")
        for _ in range(200):
            if held:break
            page.wait_for_timeout(25)
        assert len(held)==1
        page.evaluate("()=>{window.newNavigation=openAgent('farming');}")
        expect(page.locator('#workspaceName')).to_have_text('Farm Agent')
        held[0].continue_()
        page.evaluate('()=>Promise.all([window.oldNavigation,window.newNavigation])')
        expect(page.locator('#workspaceName')).to_have_text('Farm Agent')
        expect(page.locator('section#agentDetail')).to_be_visible()
        browser.close()


def test_late_agent_details_cannot_reset_newer_navigation(dashboard):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(dashboard.url)
            expect(page.locator('#chiefAgents')).to_contain_text('Job Agent')
            held = []
            page.route('**/api/domains/jobs', lambda route: held.append(route))
            page.evaluate("() => { window.pendingAgent = openAgent('jobs'); }")
            for _ in range(200):
                if held: break
                page.wait_for_timeout(25)
            assert len(held) == 1
            page.evaluate("() => show('facts')")
            expect(page.locator('section#facts')).to_be_visible()
            held[0].continue_()
            page.evaluate('() => window.pendingAgent')
            expect(page.locator('section#facts')).to_be_visible()
            expect(page.locator('nav button[aria-current="page"]')).to_have_attribute('data-target', 'facts')
        finally:
            browser.close()
