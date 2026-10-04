import json
import uuid

import pytest

from domains.farming import planning, bookkeeping, finance, journal, financial_permissions
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_bookkeeping import entry
from tests.test_farm_journal import record
from tests.test_identity_http import request


def scenario(**changes):
    p = {'name': 'Synthetic weekly plan', 'start': '2026-10-01', 'end': '2026-10-07',
         'currency': 'NGN', 'basis': 'Synthetic assumptions only; no real farm data.',
         'assumptions': {key: None for key in planning.INPUTS}}
    p.update(changes)
    return p


def save_payload(d, p, **changes):
    view = planning.overview(d.store, d.credentials['principal'])
    return {**p, 'event_id': str(uuid.uuid4()), 'expected_revision': view['revision'],
            'expected_evidence_revision': view['evidence_revision'], **changes}


def test_six_areas_exact_units_periods_and_no_double_count(dashboard):
    d = dashboard
    a = {'eggs_per_day': '10.125', 'feed_kg_at_start': '20', 'feed_kg_per_day': '2.125',
         'eggs_to_sell': 60, 'egg_price_minor': 12345, 'customer_receipts_minor': 700,
         'supplier_payments_minor': 600, 'operating_expenses_minor': 500, 'operating_budget_minor': 400}
    p = scenario(assumptions=a)
    before = finance.report(d.store, d.credentials['principal'])
    result = planning.preview(d.store, d.credentials['principal'], p)
    assert result['egg_production']['estimated_eggs'] == 70
    assert result['feed'] == {'estimated_use_kg': '14.875', 'days_available': '9.41', 'end_balance_kg': '5.125'}
    assert result['sales_value_minor'] == '740700'
    assert result['customer_receipts_minor'] == '700' and result['supplier_payments_minor'] == '600'
    assert result['operating_expenses_minor'] == '500'
    assert result['budget'] == {'operating_limit_minor': '400', 'remaining_minor': '-100', 'status': 'OVER_PLAN'}
    assert result['authority'] == 'NONE' and result['status'] == 'SCENARIO_ESTIMATE'
    planning.save(d.store, d.credentials['principal'], save_payload(d, p))
    assert finance.report(d.store, d.credentials['principal']) == before
    with d.store._connect() as con:
        assert con.execute("SELECT count(*) FROM domain_records WHERE kind='poultry_journal_v1'").fetchone()[0] == 0


def test_unknown_explicit_zero_and_stock_shortage_are_distinct(dashboard):
    d = dashboard; owner = d.credentials['principal']; p = scenario()
    r = planning.preview(d.store, owner, p)
    assert r['egg_production']['estimated_eggs'] is None and r['sales_value_minor'] is None
    assert r['feed']['days_available'] is None and r['budget']['status'] == 'NOT_SET'
    p['assumptions'].update(eggs_per_day='0', feed_kg_at_start='0', feed_kg_per_day='0',
                            operating_budget_minor=0, operating_expenses_minor=0, customer_receipts_minor=0)
    r = planning.preview(d.store, owner, p)
    assert r['egg_production']['estimated_eggs'] == 0 and r['customer_receipts_minor'] == '0'
    assert r['feed']['days_available'] is None and r['budget']['status'] == 'WITHIN_PLAN'
    p['assumptions']['feed_kg_per_day'] = '1'
    r = planning.preview(d.store, owner, p)
    assert r['feed']['end_balance_kg'] == '-7' and r['feed']['days_available'] == '0.00'
    assert any('more feed' in note for note in r['limitations'])


@pytest.mark.parametrize('key,value', [('eggs_per_day', 'NaN'), ('feed_kg_at_start', '-1'),
    ('feed_kg_per_day', '1e6'), ('eggs_per_day', 1), ('customer_receipts_minor', True),
    ('supplier_payments_minor', 1.5), ('eggs_to_sell', 0.5), ('egg_price_minor', 1000000000000)])
def test_invalid_assumptions_never_persist(key, value):
    p = scenario(); p['assumptions'][key] = value
    with pytest.raises(ValueError): planning.calculate(p)


@pytest.mark.parametrize('changes', [{'end':'2026-09-30'}, {'end':'2028-01-01'},
    {'start':'2026-02-30'}, {'currency':[]}, {'basis':''}])
def test_invalid_dates_currency_and_missing_basis(changes):
    with pytest.raises(ValueError): planning.calculate(scenario(**changes))


def test_append_only_retries_stale_review_and_evidence_changes(dashboard):
    d = dashboard; owner = d.credentials['principal']; first = save_payload(d, scenario())
    assert planning.save(d.store, owner, first)['status'] == 'RECORDED'
    saved = planning.overview(d.store, owner)['records'][0]
    assert planning.save(d.store, owner, first)['status'] == 'ALREADY_RECORDED'
    with pytest.raises(ValueError, match='conflict'):
        planning.save(d.store, owner, {**first, 'name': 'Changed retry'})
    with pytest.raises(ValueError, match='saved elsewhere'):
        planning.save(d.store, owner, {**first, 'event_id': str(uuid.uuid4())})
    p = save_payload(d, scenario())
    sale = entry(kind='SALE'); bookkeeping.append(d.store, owner, sale)
    with pytest.raises(ValueError, match='records changed'): planning.save(d.store, owner, p)
    assert planning.save(d.store, owner, first)['status'] == 'ALREADY_RECORDED'
    current = planning.overview(d.store, owner)['records'][0]
    assert current['review_needed'] and current['result'] == saved['result']
    latest = save_payload(d, scenario(name='Revised assumptions'))
    planning.save(d.store, owner, latest)
    assert planning.overview(d.store, owner, revision=first['event_id'])['records'][0]['payload'] == first
    with pytest.raises(FileNotFoundError): planning.overview(d.store, owner, revision=str(uuid.uuid4()))
    with pytest.raises(ValueError): planning.overview(d.store, owner, offset=True)


def test_role_scope_csrf_and_production_boundaries(dashboard, monkeypatch):
    d = dashboard; owner = d.credentials['principal']; service = IdentityService(d.store)
    for username, role, scope in [('plan-worker','Worker','farming'), ('plan-job','Manager','jobs'), ('plan-manager','Manager','farming')]:
        service.create_user(owner, username, PASSWORD, role, (scope,))
        raw, person = service.login(username, PASSWORD)
        allowed = username == 'plan-manager'
        assert request(d, '/api/farm/planning', raw=raw)[0] == (200 if allowed else 403)
        if allowed:
            assert planning.preview(d.store, person, scenario())['authority'] == 'NONE'
            assert planning.overview(d.store, person)['can_save'] is False
        else:
            with pytest.raises(PermissionError): planning.preview(d.store, person, scenario())
        with pytest.raises(PermissionError): planning.save(d.store, person, save_payload(d, scenario()))
    financial_permissions.configure(d.store, owner, {'event_id':str(uuid.uuid4()), 'expected_revision':None,
        'human_id':person.id, 'permissions':{**financial_permissions.DEFAULTS, 'access':False}})
    assert request(d, '/api/farm/planning', raw=raw)[0] == 403
    # Real transport enforces CSRF; a preview also crosses that boundary.
    raw_owner = d.credentials['raw']
    assert request(d, '/api/farm/planning/preview', method='POST', raw=raw_owner, csrf=False, body=scenario())[0] == 403
    monkeypatch.setenv('CHIEF_INSTANCE_MODE', 'production')
    assert request(d, '/api/farm/planning', raw=raw_owner)[0] == 403


def test_save_is_atomic_when_audit_fails(dashboard, monkeypatch):
    d = dashboard; owner = d.credentials['principal']; p = save_payload(d, scenario())
    def broken(*args, **kwargs): raise RuntimeError('Synthetic audit failure')
    original = IdentityService._event
    monkeypatch.setattr(IdentityService, '_event', broken)
    with pytest.raises(RuntimeError): planning.save(d.store, owner, p)
    assert planning.overview(d.store, owner)['record_count'] == 0
    monkeypatch.setattr(IdentityService, '_event', original)
    assert planning.save(d.store, owner, p)['status'] == 'RECORDED'


def test_saved_plan_pagination_and_concurrent_stale_write(dashboard):
    from concurrent.futures import ThreadPoolExecutor
    d=dashboard;owner=d.credentials['principal']
    left=save_payload(d,scenario(name='First competing plan'))
    right=save_payload(d,scenario(name='Second competing plan'))
    def submit(p):
        try:return planning.save(d.store,owner,p)['status']
        except ValueError as error:return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(submit,[left,right]))
    assert outcomes.count('RECORDED')==1
    assert any('saved elsewhere' in value for value in outcomes)
    for i in range(21):
        planning.save(d.store,owner,save_payload(d,scenario(name=f'Plan {i}')))
    first=planning.overview(d.store,owner);second=planning.overview(d.store,owner,offset=20)
    assert first['record_count']==22 and len(first['records'])==20 and first['next_offset']==20
    assert len(second['records'])==2 and second['next_offset'] is None
    assert not {r['payload']['event_id'] for r in first['records']} & {r['payload']['event_id'] for r in second['records']}


@pytest.mark.parametrize('width', [390, 1366])
def test_browser_planning_preserves_drafts_and_saved_versions(dashboard, width):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width':width, 'height':900})
            page.goto(dashboard.url + '/work')
            page.get_by_role('button', name='Expand all sections', exact=True).click()
            page.locator('#farmPlanning > summary').click()
            page.fill('#farmPlan_name', 'Optional weekly plan')
            page.fill('#farmPlan_start', '2026-10-01'); page.fill('#farmPlan_end', '2026-10-07')
            page.fill('#farmPlan_basis', 'Owner scenario; unknown figures left blank.')
            page.get_by_role('button', name='Preview plan', exact=True).click()
            expect(page.locator('#farmPlanOutput')).to_contain_text('Egg production: Unavailable')
            expect(page.locator('#farmPlanOutput')).to_contain_text('Operating-cost budget: Not set')
            page.get_by_role('button', name='Save this plan as a new version', exact=True).click()
            expect(page.locator('#farmPlanStatus')).to_contain_text('Plan saved as a new version')
            page.locator('#farmPlanForm summary').filter(has_text='Operating expenses').click()
            page.fill('#farmPlan_operating_expenses_minor', '123.45')
            page.fill('#farmPlan_operating_budget_minor', '100')
            page.get_by_role('button', name='Collapse all sections', exact=True).click()
            page.get_by_role('button', name='Expand all sections', exact=True).click()
            expect(page.locator('#farmPlan_operating_expenses_minor')).to_have_value('123.45')
            page.get_by_role('button', name='Preview plan', exact=True).click()
            expect(page.locator('#farmPlanOutput')).to_contain_text('NGN -23.45')
            page.get_by_role('button', name='Save this plan as a new version', exact=True).click()
            expect(page.locator('#farmPlanning > div').first.locator('details')).to_have_count(2)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        finally:
            browser.close()
