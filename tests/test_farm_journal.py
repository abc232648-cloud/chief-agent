from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import uuid

import pytest
from domains.farming import journal
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_identity_http import request


def record(**changes):
    return {'event_id': str(uuid.uuid4()), 'kind': 'feed_received', 'location': 'Feed store',
            'quantity': '25.125', 'basis': 'MEASURED',
            'observed_at': '2026-01-01T08:00:00Z', 'notes': '', 'corrects': None, 'reason': '', **changes}


def test_exact_kg_arithmetic_corrections_and_unchanged_schema(dashboard):
    d = dashboard; owner = d.credentials['principal']
    with d.store._connect() as con:
        before = list(map(tuple, con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))
    opening = record(kind='feed_opening', quantity='100.100')
    journal.append(d.store, owner, opening)
    used = record(kind='feed_used', quantity='0.200')
    journal.append(d.store, owner, used)
    journal.append(d.store, owner, record(kind='feed_used', quantity='0.100', corrects=used['event_id'], reason='Scale reading corrected'))
    data = journal.overview(d.store, owner)
    assert data['balances'][0]['recorded_balance'] == '100.000'
    assert data['record_count'] == 3
    assert used['event_id'] in [r['payload']['event_id'] for r in data['records']]
    with d.store._connect() as con:
        assert before == list(map(tuple, con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))
        assert con.execute("SELECT count(*) FROM human_security_events WHERE operation LIKE 'FARM_RECORD_%'").fetchone()[0] == 3


def test_concurrent_delivery_is_exactly_once_and_conflicts_refused(dashboard):
    d = dashboard; p = record(); owner = d.credentials['principal']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: journal.append(d.store, owner, p), range(2)))
    assert sorted(r['status'] for r in results) == ['ALREADY_RECORDED', 'RECORDED']
    with pytest.raises(ValueError): journal.append(d.store, owner, {**p, 'quantity':'26'})
    assert journal.overview(d.store, owner)['record_count'] == 1


@pytest.mark.parametrize('changes', [
    {'quantity': True}, {'quantity': 'NaN'}, {'quantity':'1e99'}, {'quantity':'-1'},
    {'quantity':'0.0001'}, {'quantity':'1000000000'}, {'kind':'mortality','quantity':'0.5'},
    {'observed_at':'2026-01-01'}, {'observed_at':utc_text(utc_now()+timedelta(days=1))},
    {'actor_id':'forged'}, {'kind':'pump_on'}, {'location':''}, {'basis':'VERIFIED'},
    {'event_id':'bad'}, {'corrects':'12345678'},
])
def test_invalid_entries_have_no_effect(dashboard, changes):
    d = dashboard
    assert request(d, '/api/farm/journal', 'POST', record(**changes), d.credentials['raw'])[0] == 400
    assert journal.overview(d.store, d.credentials['principal'])['record_count'] == 0


def test_unknown_opening_and_estimates_never_become_confirmed_counts(dashboard):
    d = dashboard; owner = d.credentials['principal']
    journal.append(d.store, owner, record(kind='mortality', quantity='3'))
    assert journal.overview(d.store, owner)['balances'][0]['recorded_balance'] is None
    journal.append(d.store, owner, record(kind='birds_opening', quantity='2343', basis='ESTIMATED'))
    b = journal.overview(d.store, owner)['balances'][0]
    assert b['recorded_balance'] == '2340' and b['contains_estimates']


def test_duplicate_opening_stale_correction_and_preopening_movement_rejected(dashboard):
    d = dashboard; owner = d.credentials['principal']; p = record(kind='feed_opening')
    journal.append(d.store, owner, p)
    with pytest.raises(ValueError): journal.append(d.store, owner, record(kind='feed_opening'))
    with pytest.raises(ValueError): journal.append(d.store, owner, record(observed_at='2025-01-01T00:00:00Z'))
    journal.append(d.store, owner, record(kind='feed_opening', corrects=p['event_id'], reason='Recount'))
    with pytest.raises(ValueError): journal.append(d.store, owner, record(kind='feed_opening', corrects=p['event_id'], reason='Stale recount'))


def test_worker_scope_revocation_and_unauthenticated_requests(dashboard):
    d = dashboard; service = IdentityService(d.store); owner = d.credentials['principal']
    farm_id = service.create_user(owner, 'farmworker', PASSWORD, 'Worker', ('farming',))
    service.create_user(owner, 'jobworker', PASSWORD, 'Worker', ('jobs',))
    farm_raw, farm = service.login('farmworker', PASSWORD); jobs_raw, jobs = service.login('jobworker', PASSWORD)
    assert request(d, '/api/farm/journal')[0] == 401
    assert request(d, '/api/farm/journal', raw=jobs_raw)[0] == 403
    assert request(d, '/api/farm/journal', 'POST', record(), jobs_raw)[0] == 403
    assert request(d, '/api/farm/journal', 'POST', record(kind='feed_opening'), farm_raw)[0] == 403
    p = record(); journal.append(d.store, farm, p)
    with pytest.raises(PermissionError): journal.append(d.store, farm, record(corrects=p['event_id'], reason='Correction'))
    with pytest.raises(ValueError): journal.append(d.store, owner, p)
    service.disable_user(owner, farm_id)
    with pytest.raises(PermissionError): journal.append(d.store, farm, record())


@pytest.mark.parametrize('headers', [{'csrf':False}, {'origin':False}, {'extra':{'Origin':'https://untrusted.invalid'}}, {'extra':{'Host':'untrusted.invalid'}}])
def test_existing_transport_protections_cover_farm_entry(dashboard, headers):
    assert request(dashboard, '/api/farm/journal', 'POST', record(), dashboard.credentials['raw'], **headers)[0] == 403


def test_pilot_refuses_production(dashboard, monkeypatch):
    monkeypatch.setenv('CHIEF_INSTANCE_MODE', 'production')
    assert request(dashboard, '/api/farm/journal', raw=dashboard.credentials['raw'])[0] == 403


def test_disabled_farm_or_record_component_blocks_entries(dashboard):
    from application.composition import default_registry
    from control.agents import AgentControls
    from capabilities.contracts import Node, Mode
    from database.component_state import ComponentState
    d = dashboard; owner = d.credentials['principal']
    controls = AgentControls(d.store, default_registry())
    controls.change('farming', {'enabled':False})
    with pytest.raises(PermissionError): journal.append(d.store, owner, record())
    controls.change('farming', {'enabled':True, 'running':False})
    journal.append(d.store, owner, record())  # Human observation while automation is paused.
    with d.store._connect() as con:
        ComponentState(d.store).put(con, Node('capability','farming.records.write'), Mode.DISABLED, actor=owner.id, reason='Synthetic test')
    with pytest.raises(PermissionError): journal.append(d.store, owner, record())


def test_browser_entry_and_correction(dashboard):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(); page.goto(dashboard.url)
            expect(page.locator('#chiefAgents')).to_contain_text('Farm Agent')
            page.locator('nav .domainNav[data-domain="farming"] > summary').click()
            page.get_by_role('button', name='Poultry records', exact=True).click()
            page.get_by_role('button', name='Expand all sections', exact=True).click()
            expect(page.locator('#farmBalances')).to_contain_text('No reports yet')
            page.select_option('#farmKind', 'feed_opening')
            page.fill('#farmLocation', 'Feed store'); page.fill('#farmQuantity', '100.1')
            page.click('#farmSave'); expect(page.locator('#farmResult')).to_have_text('Record saved.')
            expect(page.locator('#farmBalances')).to_contain_text('100.1 kg')
            page.get_by_role('button', name='Correct record').click()
            page.fill('#farmQuantity', '99.9'); page.fill('#farmReason', 'Scale reading corrected')
            page.click('#farmSave')
            expect(page.locator('#farmBalances')).to_contain_text('99.9 kg')
            expect(page.locator('#farmHistory')).to_contain_text('Scale reading corrected')
            assert journal.overview(dashboard.store, dashboard.credentials['principal'])['record_count'] == 2
        finally:
            browser.close()
