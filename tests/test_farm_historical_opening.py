import pytest
from domains.farming import journal, costing
from tests.test_farm_journal import record


def test_historical_activity_updates_period_costs_without_double_counting_opening(dashboard):
    d=dashboard;p=d.credentials['principal']
    opening=record(kind='eggs_opening',quantity='100',observed_at='2026-02-01T00:00:00Z')
    journal.append(d.store,p,opening)
    old=record(kind='eggs_collected',quantity='50',observed_at='2026-01-01T00:00:00Z',historical_before_opening=True)
    journal.append(d.store,p,old)
    assert journal.append(d.store,p,old)['status']=='ALREADY_RECORDED'
    journal.append(d.store,p,record(kind='eggs_collected',quantity='10',observed_at='2026-02-02T00:00:00Z'))
    assert journal.overview(d.store,p)['balances'][0]['recorded_balance']=='110'
    def past():return costing.report(d.store,p,start='2026-01-01',end='2026-01-31',currency='NGN')
    assert past()['collected_eggs']==50
    correction={**old,'event_id':'historical-correction-1','quantity':'40','corrects':old['event_id'],'reason':'Recovered original register'}
    journal.append(d.store,p,correction)
    assert past()['collected_eggs']==40
    before=journal.overview(d.store,p)
    assert before['balances'][0]['recorded_balance']=='110'
    from tests.test_farm_indexes import upgrade
    upgrade(d)  # Verified B.5 backup/isolated restore before additive index migration.
    assert journal.overview(d.store,p)==before
    assert journal.append(d.store,p,old)['status']=='ALREADY_RECORDED'


def test_historical_flag_requires_earlier_time_and_cannot_change_correction_class(dashboard):
    d=dashboard;p=d.credentials['principal']
    with pytest.raises(ValueError,match='existing later opening'):
        journal.append(d.store,p,record(historical_before_opening=True))
    journal.append(d.store,p,record(kind='feed_opening',quantity='20',observed_at='2026-02-01T00:00:00Z'))
    with pytest.raises(ValueError,match='predates'):
        journal.append(d.store,p,record(kind='feed_used',quantity='2'))
    old=record(kind='feed_used',quantity='2',historical_before_opening=True)
    journal.append(d.store,p,old)
    with pytest.raises(ValueError,match='historical classification'):
        journal.append(d.store,p,record(kind='feed_used',quantity='1',corrects=old['event_id'],reason='Test'))
    with pytest.raises(ValueError,match='existing later opening'):
        journal.append(d.store,p,record(kind='feed_used',quantity='2',observed_at='2026-02-02T00:00:00Z',historical_before_opening=True))


def test_historical_import_does_not_change_physical_count_difference(dashboard):
    from domains.farming import setup, reconciliation
    from tests.test_farm_setup import entity
    from tests.test_farm_reconciliation import count, decide
    d=dashboard;p=d.credentials['principal'];e=entity();setup.append(d.store,p,e)
    journal.append(d.store,p,record(kind='feed_opening',quantity='100',entity_id=e['entity_id'],location=e['name'],observed_at='2026-02-01T00:00:00Z'))
    journal.append(d.store,p,record(kind='feed_received',quantity='50',entity_id=e['entity_id'],location=e['name'],historical_before_opening=True))
    physical=count(e,quantity='150',observed_at='2026-02-02T00:00:00Z')
    saved=reconciliation.append(d.store,p,physical)['record']
    assert saved['recorded_balance']=='100' and saved['difference']=='50'
    reconciliation.append(d.store,p,decide(physical))
    assert journal.overview(d.store,p)['balances'][0]['recorded_balance']=='150'


def test_browser_distinguishes_history_from_current_stock(dashboard):
    from playwright.sync_api import sync_playwright, expect
    d=dashboard;p=d.credentials['principal']
    journal.append(d.store,p,record(kind='eggs_opening',quantity='100',observed_at='2026-02-01T00:00:00Z'))
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work')
            page.get_by_role('button',name='Expand all sections',exact=True).click()
            expect(page.locator('#farmPhysicalCounts > summary')).to_have_text('Current stock: count and adjustment')
            expect(page.locator('#farmEntrySection > summary')).to_have_text('Record activity or update history')
            page.select_option('#farmKind','eggs_collected');page.fill('#farmLocation','Feed store')
            page.fill('#farmQuantity','50');page.fill('#farmObserved','2026-01-01T01:00')
            page.locator('#farmHistorical').check();page.locator('#farmSave').click()
            expect(page.locator('#farmResult')).to_have_text('Record saved.')
            expect(page.locator('#farmBalances')).to_contain_text('Recorded balance: 100 eggs')
            expect(page.locator('#farmHistory')).to_contain_text('Historical activity before opening count; excluded from current stock')
        finally:browser.close()
