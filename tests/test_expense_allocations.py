import uuid
import pytest
from domains.farming import expense_allocations as allocations, bookkeeping as books, setup, costing
from tests.test_farm_bookkeeping import entry
from tests.test_farm_setup import entity
from tests.test_identity_http import request
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD


def seed(d):
    p = d.credentials['principal']
    house = {**entity(), 'entity_type': 'HOUSE', 'name': 'House'}
    setup.append(d.store, p, house)
    flocks = [{**entity(), 'entity_type': 'FLOCK', 'house_id': house['entity_id'], 'name': name} for name in ('Flock A', 'Flock B')]
    for flock in flocks:
        setup.append(d.store, p, flock)
    expense = entry(kind='EXPENSE_CLAIM', amount_minor=10000)
    books.append(d.store, p, expense)
    return expense, flocks


def command(expense, flocks, amount=3000):
    return dict(event_id=str(uuid.uuid4()), expense_id=expense['event_id'], expected_revision=None,
                allocations=[{'flock_id': f['entity_id'], 'amount_minor': amount} for f in flocks], reason='Explicit shares from expense register')


def report(d, currency='NGN'):
    return costing.report(d.store, d.credentials['principal'], start='2026-01-01', end='2026-01-01', currency=currency)


def test_split_conserves_money_stock_schema_and_currencies(dashboard):
    d = dashboard; p = d.credentials['principal']; expense, flocks = seed(d)
    with d.store._connect() as con:
        before = books.rows(con)
        schema = list(map(tuple, con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))
        stock = list(map(tuple, con.execute("SELECT * FROM domain_records WHERE kind='poultry_journal_v1'")))
    cmd = command(expense, flocks)
    assert allocations.append(d.store, p, cmd)['status'] == 'RECORDED'
    assert allocations.append(d.store, p, cmd)['status'] == 'ALREADY_RECORDED'
    result = report(d)
    assert sum(f['allocated_minor'] for f in result['flock_expenses']) == 6000
    assert result['unallocated_expenses_minor'] == 4000
    assert cmd['event_id'] in result['source_references']
    assert report(d, 'USD')['flock_expenses'] == []
    assert report(d, 'USD')['unallocated_expenses_minor'] == 0
    with d.store._connect() as con:
        assert before == books.rows(con)
        assert schema == list(map(tuple, con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))
        assert stock == list(map(tuple, con.execute("SELECT * FROM domain_records WHERE kind='poultry_journal_v1'")))


@pytest.mark.parametrize('amount', [0, -1, True, 1.2, 6000])
def test_invalid_or_excess_shares_are_atomic(dashboard, amount):
    d = dashboard; expense, flocks = seed(d)
    with pytest.raises(ValueError):
        allocations.append(d.store, d.credentials['principal'], command(expense, flocks, amount))
    assert allocations.overview(d.store, d.credentials['principal'])['history'] == []


def test_revision_retry_clear_and_void_preserve_history(dashboard):
    d = dashboard; p = d.credentials['principal']; expense, flocks = seed(d); cmd = command(expense, flocks)
    allocations.append(d.store, p, cmd)
    with pytest.raises(ValueError, match='changed'):
        allocations.append(d.store, p, command(expense, flocks))
    with pytest.raises(ValueError, match='conflict'):
        allocations.append(d.store, p, {**cmd, 'reason': 'Changed retry'})
    clear = {**command(expense, flocks), 'expected_revision': cmd['event_id'], 'allocations': []}
    allocations.append(d.store, p, clear)
    assert report(d)['unallocated_expenses_minor'] == 10000
    allocations.append(d.store, p, {**command(expense, flocks), 'expected_revision': clear['event_id']})
    books.append(d.store, p, entry(kind='VOID', reference=expense['event_id'], amount_minor=0))
    result = report(d)
    assert result['flock_expenses'] == [] and result['unallocated_expenses_minor'] == 0
    assert len(allocations.overview(d.store, p)['history']) == 3
    with pytest.raises(ValueError):
        allocations.append(d.store, p, command(expense, flocks))


def test_unknown_duplicate_and_non_flock_rejected(dashboard):
    d = dashboard; p = d.credentials['principal']; expense, flocks = seed(d)
    for targets in ([flocks[0], flocks[0]], [{'entity_id': str(uuid.uuid4())}], [{'entity_id': flocks[0]['house_id']}]):
        with pytest.raises(ValueError):
            allocations.append(d.store, p, command(expense, targets))
    assert allocations.overview(d.store, p)['history'] == []


def test_permissions_and_browser(dashboard):
    from playwright.sync_api import sync_playwright, expect
    d = dashboard; p = d.credentials['principal']; expense, flocks = seed(d); svc = IdentityService(d.store)
    for role, domains in [('Worker', ('farming',)), ('Manager', ('farming',)), ('Owner', ('jobs',))]:
        name = 'allocation-' + role; svc.create_user(p, name, PASSWORD, role, domains); _, other = svc.login(name, PASSWORD)
        with pytest.raises(PermissionError):
            allocations.append(d.store, other, command(expense, flocks))
        if role != 'Manager':
            with pytest.raises(PermissionError): allocations.overview(d.store, other)
    assert request(d, '/api/farm/expense-allocations', raw='bad')[0] == 401
    assert request(d, '/api/farm/expense-allocations', 'POST', command(expense, flocks), raw=d.credentials['raw'], csrf=False)[0] == 403
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(); page.goto(d.url + '/work'); page.get_by_role('button', name='Expand all sections', exact=True).click()
            host = page.locator('#farmExpenseAllocations')
            host.get_by_role('button', name='Add flock share', exact=True).click()
            host.get_by_label('Flock receiving this expense share', exact=True).select_option(flocks[0]['entity_id'])
            host.get_by_label('Expense share (NGN)', exact=True).fill('30.00')
            host.get_by_label('Reason for expense allocation', exact=True).fill('Recorded flock share')
            host.get_by_role('button', name='Save expense allocation', exact=True).click()
            expect(host.get_by_role('status')).to_contain_text('No additional expense')
            assert report(d)['unallocated_expenses_minor'] == 7000
        finally:
            browser.close()
