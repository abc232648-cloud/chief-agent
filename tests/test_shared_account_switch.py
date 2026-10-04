"""Synthetic shared-browser acceptance: old financial DOM must not survive account change."""
from playwright.sync_api import sync_playwright,expect
from identity.service import IdentityService
from domains.farming import bookkeeping
from tests.test_farm_bookkeeping import entry
from tests.checkpoint_f_fixture import PASSWORD



def test_other_account_login_removes_previous_financial_view(dashboard):
    d=dashboard;identity=IdentityService(d.store)
    identity.create_user(d.credentials['principal'],'switch-worker',PASSWORD,'Worker',('farming',))
    bookkeeping.append(d.store,d.credentials['principal'],entry(counterparty='PRIVATE_PREVIOUS_ACCOUNT_BUYER'))
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);context=browser.new_context()
        try:
            first=context.new_page();first.goto(d.url+'/work')
            first.get_by_role('button',name='Expand all sections',exact=True).click()
            expect(first.locator('#farmFinanceCard')).to_contain_text('PRIVATE_PREVIOUS_ACCOUNT_BUYER')
            second=context.new_page();second.goto(d.url+'/work-login')
            second.fill('#username','switch-worker');second.fill('#password',PASSWORD)
            second.locator('#sign-in button').click()
            expect(second.locator('#workRole')).to_have_text('WORKER')
            # Wait for account change to remove prior sensitive view, not merely deny new requests.
            expect(first.locator('body')).not_to_contain_text('PRIVATE_PREVIOUS_ACCOUNT_BUYER')
        finally:browser.close()
