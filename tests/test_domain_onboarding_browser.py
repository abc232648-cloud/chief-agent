"""Fresh installation acceptance: no configured-workspace fixture here."""
import pytest
from playwright.sync_api import sync_playwright, expect
from test_dashboard_runtime import request
from browser_navigation import navigate


@pytest.mark.parametrize('choice', ['job_only','farm_only','both'])
def test_first_opening_explicit_choice_and_reload(dashboard, choice):
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url)
            dialog=page.locator('#domainSelectionDialog')
            expect(dialog).to_be_visible()
            expect(dialog.locator('input:checked')).to_have_count(0)
            expect(page.locator('#confirmDomainSelectionBtn')).to_be_disabled()
            dialog.locator('input[value="'+choice+'"]').check()
            page.locator('#confirmDomainSelectionBtn').click()
            expect(dialog).not_to_be_visible()
            assert request(dashboard,'/api/domains/selection')[2]['domain_selection']==choice
            page.reload();expect(page.locator('#chiefAgents')).to_contain_text('Farm Agent' if choice=='farm_only' else 'Job Agent')
            expect(dialog).not_to_be_visible()
            states={a['id']:a for a in request(dashboard,'/api/chief')[2]['agents']}
            assert bool(states['jobs']['enabled']) == (choice!='farm_only')
            assert bool(states['farming']['enabled']) == (choice!='job_only')
        finally:browser.close()


def test_setup_error_preserves_choice_and_allows_retry(dashboard):
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url)
            dialog=page.locator('#domainSelectionDialog');expect(dialog).to_be_visible()
            dialog.locator('input[value="both"]').check()
            page.route('**/api/domains/selection',lambda route:route.fulfill(status=500,content_type='application/json',body='{"reason":"Synthetic setup failure"}'))
            page.locator('#confirmDomainSelectionBtn').click()
            expect(page.locator('#domainSelectionStatus')).to_contain_text('Synthetic setup failure')
            expect(dialog).to_be_visible();expect(page.locator('#confirmDomainSelectionBtn')).to_be_enabled()
            assert request(dashboard,'/api/domains/selection')[2]['domain_selection'] is None
            page.unroute('**/api/domains/selection')
            page.locator('#confirmDomainSelectionBtn').click();expect(dialog).not_to_be_visible()
        finally:browser.close()


@pytest.mark.usefixtures('configured_both_workspace')
def test_shared_session_job_home_and_global_schedule_heading(dashboard):
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url)
            navigate(page,'domains')
            page.locator('#domainList [data-action="open-agent"][data-id="farming"]').click()
            expect(page.locator('#workspaceName')).to_have_text('Farm Agent')
            navigate(page,'scheduler');expect(page.locator('#workspaceName')).to_have_text('Chief Agent')
            navigate(page,'domains')
            page.locator('#domainList [data-action="open-agent"][data-id="jobs"]').click()
            expect(page.locator('#jobOverview')).to_be_visible()
            expect(page.locator('#jobAgentControlsCard')).to_contain_text('Job Agent')
            expect(page.locator('#jobOverview [data-action="open-agent"]')).to_have_count(0)
            control=page.locator('#jobAgentControlsCard [data-key="running"]')
            control.click();expect(control).to_have_text('Start agent')
            control.click();expect(control).to_have_text('Pause agent')
            assert '/login' not in page.url
        finally:browser.close()


@pytest.mark.usefixtures('configured_both_workspace')
def test_owner_can_reopen_workspace_selection(dashboard):
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url)
            navigate(page,'domains')
            page.get_by_role('button',name='Change workspace selection').click()
            dialog=page.locator('#domainSelectionDialog');expect(dialog).to_be_visible()
            page.evaluate('loadChief()')
            expect(dialog).to_be_visible()
            dialog.locator('input[value="farm_only"]').check()
            page.locator('#confirmDomainSelectionBtn').click()
            expect(dialog).not_to_be_visible()
            assert request(dashboard,'/api/domains/selection')[2]['domain_selection']=='farm_only'
            page.reload();expect(page.locator('#chiefAgents')).to_contain_text('Farm Agent')
            expect(dialog).not_to_be_visible()
        finally:browser.close()
