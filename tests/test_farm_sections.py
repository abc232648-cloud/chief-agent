"""Real disclosure navigation at phone and laptop sizes; keep unsaved work."""
import pytest
from playwright.sync_api import sync_playwright, expect
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD

@pytest.mark.parametrize('width,route',[(390,'/work'),(1366,'/work'),(1366,'/')])
def test_collapsed_sections_preserve_drafts_and_keyboard_navigation(dashboard,width,route):
    d=dashboard
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':width,'height':850});page.goto(d.url+route)
            if route=='/':
                page.locator('nav .domainNav[data-domain="farming"] > summary').click()
                page.get_by_role('button',name='Poultry records',exact=True).click()
            expect(page.locator('#farmEntrySection')).to_be_visible()
            expect(page.locator('#farmRecords > details.farmSection[open]')).to_have_count(0)
            assert page.locator('#farmRecords > details.farmSection > summary').first.inner_text()=='Record activity or update history'
            summary=page.locator('#farmEntrySection > summary');summary.focus();page.keyboard.press('Enter')
            expect(page.locator('#farmQuantity')).to_be_visible()
            page.fill('#farmQuantity','12');page.fill('#farmNotes','Unfinished report')
            summary.click();expect(page.locator('#farmQuantity')).to_be_hidden()
            summary.click();expect(page.locator('#farmQuantity')).to_have_value('12')
            expect(page.locator('#farmNotes')).to_have_value('Unfinished report')
            page.get_by_role('button',name='Expand all sections',exact=True).click()
            expect(page.locator('#farmFinanceForm')).to_be_visible()
            page.get_by_role('button',name='Collapse all sections',exact=True).click()
            expect(page.locator('#farmRecords > details.farmSection[open]')).to_have_count(0)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            import os
            if os.environ.get('CHIEF_UI_CAPTURE') and route=='/work':
                page.screenshot(path=os.path.join(os.environ['CHIEF_UI_CAPTURE'],f'farm-collapsed-{width}.png'),full_page=True)
        finally:browser.close()


def test_worker_expand_does_not_reveal_privileged_sections(dashboard):
    d=dashboard;s=IdentityService(d.store)
    s.create_user(d.credentials['principal'],'compact-worker',PASSWORD,'Worker',('farming',));raw,_=s.login('compact-worker',PASSWORD)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':390,'height':850})
            page.context.add_cookies([{'name':'chief_session','value':raw,'url':d.url,'httpOnly':True,'sameSite':'Strict'}]);page.goto(d.url+'/work')
            expect(page.locator('#workRole')).to_have_text('WORKER')
            page.get_by_role('button',name='Expand all sections',exact=True).click()
            for ident in ['farmFinanceCard','farmSetupCard','farmRoleCard','farmHistorySection','farmBalancesSection']:
                expect(page.locator('#'+ident)).to_be_hidden()
            expect(page.locator('#farmRecordForm')).to_be_visible()
        finally:browser.close()


def test_owner_can_revisit_bookkeeping_from_chief_home(dashboard):
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url)
            for visit in range(2):
                page.locator('nav .domainNav[data-domain="farming"] > summary').click()
                page.get_by_role('button',name='Poultry records',exact=True).click()
                page.get_by_role('button',name='Expand all sections',exact=True).click()
                expect(page.locator('#financeSummary')).to_be_visible()
                if visit==0:
                    page.locator('button[data-action="show"][data-target="overview"]').click()
                    expect(page.locator('#chiefAgents')).to_be_visible()
        finally:browser.close()
