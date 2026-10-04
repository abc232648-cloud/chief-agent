import json
import pytest
from domains.farming import journal, units, costing_policy
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_journal import record
from tests.test_farm_costing_policy import assumptions


def test_exact_conversion_pinned_history_and_no_financial_leak(dashboard):
    d=dashboard;p=d.credentials['principal'];settings=assumptions(eggs_per_crate=30,kg_per_bag='25.125')
    costing_policy.configure(d.store,p,settings)
    event=record(kind='eggs_collected',quantity='60',conversion={'unit':'crates','quantity':'2','policy_revision':settings['event_id']})
    journal.append(d.store,p,event)
    later=assumptions(expected_revision=settings['event_id'],eggs_per_crate=24,kg_per_bag='50')
    costing_policy.configure(d.store,p,later)
    assert journal.append(d.store,p,event)['status']=='ALREADY_RECORDED'
    with pytest.raises(ValueError,match='does not match'):
        journal.append(d.store,p,record(kind='eggs_collected',quantity='60',conversion={'unit':'crates','quantity':'2','policy_revision':later['event_id']}))
    journal.append(d.store,p,record(kind='feed_received',quantity='50.250',conversion={'unit':'bags','quantity':'2','policy_revision':settings['event_id']}))
    service=IdentityService(d.store);service.create_user(p,'unit-worker',PASSWORD,'Worker',('farming',));_,worker=service.login('unit-worker',PASSWORD)
    public=units.overview(d.store,worker)
    assert set(public)=={'revision','eggs_per_crate','kg_per_bag'}
    assert 'feed_minor_per_kg' not in json.dumps(public)


def test_missing_sizes_and_wrong_stock_rejected(dashboard):
    d=dashboard;p=d.credentials['principal'];settings=assumptions();costing_policy.configure(d.store,p,settings)
    for kind,unit in [('eggs_collected','crates'),('feed_received','bags'),('birds_arrived','crates')]:
        with pytest.raises(ValueError):journal.append(d.store,p,record(kind=kind,quantity='2',conversion={'unit':unit,'quantity':'2','policy_revision':settings['event_id']}))


def test_browser_crates_preview_and_saved_base_quantity(dashboard):
    from playwright.sync_api import sync_playwright, expect
    d=dashboard;p=d.credentials['principal'];costing_policy.configure(d.store,p,assumptions(eggs_per_crate=30))
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work')
            page.get_by_role('button',name='Expand all sections',exact=True).click()
            page.select_option('#farmKind','eggs_collected');page.select_option('#farmEntryUnit','crates')
            page.fill('#farmQuantity','2');page.fill('#farmLocation','Synthetic flock')
            expect(page.locator('#farmConversionPreview')).to_contain_text('60.000 eggs')
            page.locator('#farmSave').click();expect(page.locator('#farmResult')).to_have_text('Record saved.')
            saved=journal.overview(d.store,p)['records'][0]['payload']
            assert saved['quantity']=='60.000' and saved['conversion']['quantity']=='2'
        finally:browser.close()


def test_physical_count_conversion_keeps_approval_and_original_size(dashboard):
    from domains.farming import reconciliation as counts
    from tests.test_farm_reconciliation import stock,count,decide
    d=dashboard;p=d.credentials['principal'];e=stock(d)
    settings=assumptions(kg_per_bag='25');costing_policy.configure(d.store,p,settings)
    physical=count(e,quantity='50',conversion={'unit':'bags','quantity':'2','policy_revision':settings['event_id']})
    result=counts.append(d.store,p,physical)['record'];assert result['difference']=='40.000'
    costing_policy.configure(d.store,p,assumptions(expected_revision=settings['event_id'],kg_per_bag='50'))
    assert counts.append(d.store,p,physical)['status']=='ALREADY_RECORDED'
    assert journal.overview(d.store,p)['balances'][0]['recorded_balance']=='10.000'
    counts.append(d.store,p,decide(physical))
    assert journal.overview(d.store,p)['balances'][0]['recorded_balance']=='50.000'
    with pytest.raises(ValueError,match='does not match'):
        counts.append(d.store,p,count(e,quantity='100',conversion={'unit':'bags','quantity':'2','policy_revision':settings['event_id']}))


def test_browser_physical_bag_count_preview(dashboard):
    from tests.test_farm_reconciliation import stock
    from domains.farming import reconciliation as counts
    from playwright.sync_api import sync_playwright, expect
    d=dashboard;p=d.credentials['principal'];e=stock(d);costing_policy.configure(d.store,p,assumptions(kg_per_bag='25'))
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work')
            page.get_by_role('button',name='Expand all sections',exact=True).click()
            page.select_option('#farmPhysicalEntity',e['entity_id']);page.select_option('#farmPhysicalUnit','bags');page.fill('#farmPhysicalQuantity','2')
            page.fill('#farmPhysicalReason','Two physically counted bags')
            expect(page.locator('#farmPhysicalPreview')).to_contain_text('50.000 kg')
            page.get_by_role('button',name='Save physical count for review',exact=True).click()
            expect(page.locator('#farmPhysicalCountResult')).to_contain_text('Stock is unchanged.')
            saved=counts.overview(d.store,p)['items'][0]
            assert saved['payload']['quantity']=='50.000' and saved['state']=='PENDING'
            assert journal.overview(d.store,p)['balances'][0]['recorded_balance']=='10.000'
        finally:browser.close()
