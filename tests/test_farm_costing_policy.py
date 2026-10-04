import uuid
import pytest
from domains.farming import costing_policy as policy, journal, financial_permissions, brief
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_journal import record


def assumptions(**kw):
    return {'event_id':str(uuid.uuid4()),'expected_revision':None,'currency':'NGN','feed_minor_per_kg':200,'eggs_per_crate':None,'kg_per_bag':None,'reason':'Synthetic explicit estimate',**kw}


def test_pinned_valuation_history_permissions_and_unknown_units(dashboard):
    d=dashboard;p=d.credentials['principal']
    journal.append(d.store,p,record(kind='feed_opening',quantity='20'))
    journal.append(d.store,p,record(kind='feed_used',quantity='2'))
    journal.append(d.store,p,record(kind='eggs_collected',quantity='10'))
    first=assumptions();policy.configure(d.store,p,first)
    assert policy.configure(d.store,p,first)['status']=='ALREADY_RECORDED'
    second=assumptions(expected_revision=first['event_id'],feed_minor_per_kg=400,eggs_per_crate=30,kg_per_bag='25')
    policy.configure(d.store,p,second)
    old=policy.report(d.store,p,start='2026-01-01',end='2026-01-01',revision=first['event_id'])
    assert old['cost_per_collected_egg_minor']=='40.0000' and old['eggs_per_crate'] is None
    assert policy.report(d.store,p,start='2026-01-01',end='2026-01-01')['cost_per_collected_egg_minor']=='80.0000'
    with pytest.raises(ValueError,match='changed'):policy.configure(d.store,p,assumptions())
    service=IdentityService(d.store);service.create_user(p,'cost-manager',PASSWORD,'Manager',('farming',));_,manager=service.login('cost-manager',PASSWORD)
    with pytest.raises(PermissionError):policy.configure(d.store,manager,assumptions())
    financial_permissions.configure(d.store,p,{'event_id':str(uuid.uuid4()),'human_id':manager.id,'expected_revision':None,'permissions':{**financial_permissions.DEFAULTS,'access':False}})
    assert 'finance' not in brief.overview(d.store,manager,'2026-01-01')
    with pytest.raises(PermissionError):policy.overview(d.store,manager)


def test_browser_costing_settings_and_missing_evidence(dashboard):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url+'/work')
            page.get_by_role('button',name='Expand all sections',exact=True).click()
            page.fill('#farmCostReason','No supported prices yet')
            page.get_by_role('button',name='Save costing settings',exact=True).click()
            expect(page.locator('#farmCostStatus')).to_have_text('Costing settings saved as a new version.')
            page.get_by_role('button',name='Calculate recorded costs',exact=True).click()
            expect(page.locator('#farmCostOutput')).to_contain_text('Unavailable')
            expect(page.locator('#farmCostOutput')).to_contain_text('Feed valuation is not supplied.')
        finally:browser.close()



def test_recorded_price_needs_source_and_estimates_never_become_expenses(dashboard):
    from domains.farming import finance
    d=dashboard;p=d.credentials['principal']
    invalid=assumptions(price_basis='RECORDED_PRICE',price_source='')
    with pytest.raises(ValueError,match='reference'):policy.configure(d.store,p,invalid)
    recorded={**invalid,'price_source':'Synthetic feed invoice INV-10'}
    policy.configure(d.store,p,recorded)
    result=policy.report(d.store,p,start='2026-01-01',end='2026-01-01')
    assert result['price_basis']=='RECORDED_PRICE' and result['price_source']=='Synthetic feed invoice INV-10'
    estimated=assumptions(expected_revision=recorded['event_id'],price_basis='PLANNING_ESTIMATE',price_source='Owner planning scenario')
    policy.configure(d.store,p,estimated)
    result=policy.report(d.store,p,start='2026-01-01',end='2026-01-01')
    assert result['price_basis']=='PLANNING_ESTIMATE'
    assert any('not an actual recorded expense' in t for t in result['limitations'])
    assert finance.report(d.store,p)['summaries']=={}
    with pytest.raises(ValueError):policy.configure(d.store,p,assumptions(price_basis='UNKNOWN',price_source=''))


def test_browser_price_entry_uses_currency_and_exact_display(dashboard):
    from playwright.sync_api import sync_playwright, expect
    d=dashboard;owner=d.credentials['principal']
    journal.append(d.store,owner,record(kind='feed_opening',quantity='10'))
    journal.append(d.store,owner,record(kind='feed_used',quantity='2'))
    journal.append(d.store,owner,record(kind='eggs_collected',quantity='10'))
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work')
            page.get_by_role('button',name='Expand all sections',exact=True).click()
            page.fill('#farmCostFeed','123.45');page.select_option('#farmCostBasis','PLANNING_ESTIMATE')
            page.fill('#farmCostReason','Synthetic currency entry')
            page.get_by_role('button',name='Save costing settings',exact=True).click()
            expect(page.locator('#farmCostStatus')).to_contain_text('saved as a new version')
            assert policy.overview(d.store,owner)['policies'][-1]['payload']['feed_minor_per_kg']==12345
            page.fill('#farmCostStart','2026-01-01');page.fill('#farmCostEnd','2026-01-01')
            page.get_by_role('button',name='Calculate recorded costs',exact=True).click()
            expect(page.locator('#farmCostOutput')).to_contain_text('Consumed feed valuation: NGN 246.90')
            expect(page.locator('#farmCostOutput')).to_contain_text('Cost per collected egg: NGN 24.6900')
            assert page.evaluate("farmCostMoney('9999999999999999.995','NGN')")=='NGN 100000000000000.00'
        finally:browser.close()
