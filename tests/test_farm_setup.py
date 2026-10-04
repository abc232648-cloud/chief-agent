import uuid
import pytest
from domains.farming import setup, journal
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_journal import record
from tests.test_identity_http import request


def entity(**changes):
    return dict(event_id=str(uuid.uuid4()), operation='create_entity', entity_id=str(uuid.uuid4()),
                entity_type='FEED_STORE', name='Main store', opened_on='2025-01-01', house_id=None, **changes)


def assign(store, owner, human, role):
    return setup.append(store, owner, dict(event_id=str(uuid.uuid4()), operation='assign_role',
                                          human_id=human, role=role, reason='Synthetic role assignment'))


def test_stable_id_keeps_same_named_stores_separate_and_legacy_intact(dashboard):
    d=dashboard; owner=d.credentials['principal']
    a=entity(); b=entity()
    setup.append(d.store, owner, a); setup.append(d.store, owner, b)
    for item, quantity in [(a,'10'),(b,'20')]:
        journal.append(d.store, owner, record(kind='feed_opening', location=item['name'], entity_id=item['entity_id'], quantity=quantity))
    journal.append(d.store, owner, record(kind='feed_opening', location=a['name'], quantity='30'))
    assert sorted(x['recorded_balance'] for x in journal.overview(d.store, owner)['balances'])==['10','20','30']
    with pytest.raises(ValueError):
        journal.append(d.store, owner, record(entity_id=a['entity_id'], location='Forged label'))


def test_setup_idempotency_parent_dates_and_schema(dashboard):
    d=dashboard; owner=d.credentials['principal']
    with d.store._connect() as con: before=list(map(tuple,con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))
    p=entity(); assert setup.append(d.store,owner,p)['status']=='RECORDED'
    assert setup.append(d.store,owner,p)['status']=='ALREADY_RECORDED'
    with pytest.raises(ValueError):setup.append(d.store,owner,{**p,'name':'Changed'})
    with pytest.raises(ValueError):setup.append(d.store,owner,{**entity(),'entity_type':'FLOCK','house_id':p['entity_id']})
    with d.store._connect() as con: assert before==list(map(tuple,con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))


def test_supervisor_is_domain_assignment_not_chief_admin(dashboard):
    d=dashboard; owner=d.credentials['principal']; service=IdentityService(d.store)
    uid=service.create_user(owner,'supervisor',PASSWORD,'Worker',('farming',))
    raw, worker=service.login('supervisor',PASSWORD)
    assign(d.store,owner,uid,'SUPERVISOR')
    assert service.refresh(worker).role=='Worker'
    journal.append(d.store,worker,record(kind='feed_opening'))
    with pytest.raises(PermissionError):setup.append(d.store,worker,entity())
    with pytest.raises(PermissionError):service.authorize(worker,'installation.manage')
    with pytest.raises(PermissionError):service.authorize(worker,'work.read','jobs')
    assign(d.store,owner,uid,'WORKER')
    with pytest.raises(PermissionError):journal.append(d.store,worker,record(kind='birds_opening', quantity='10'))
    service.disable_user(owner,uid)
    assert request(d,'/api/farm/setup',raw=raw)[0]==401


def test_manager_cannot_assign_or_owner_approve(dashboard):
    d=dashboard; owner=d.credentials['principal']; service=IdentityService(d.store)
    uid=service.create_user(owner,'manager',PASSWORD,'Manager',('farming',))
    _,manager=service.login('manager',PASSWORD)
    setup.append(d.store,manager,entity())
    with pytest.raises(PermissionError):assign(d.store,manager,uid,'SUPERVISOR')
    with d.store._connect() as con:
        with pytest.raises(PermissionError):setup.authorize(d.store,con,manager,'approve')


def test_general_manager_assignment_never_promotes_worker_account(dashboard):
    d=dashboard;owner=d.credentials['principal'];service=IdentityService(d.store)
    uid=service.create_user(owner,'unpromoted-worker',PASSWORD,'Worker',('farming',))
    with pytest.raises(ValueError):assign(d.store,owner,uid,'GENERAL_MANAGER')
    assert service.login('unpromoted-worker',PASSWORD)[1].role=='Worker'


def test_setup_http_and_pagination(dashboard,monkeypatch):
    d=dashboard; raw=d.credentials['raw']; owner=d.credentials['principal']
    assert request(d,'/api/farm/setup','POST',entity(),raw)[0]==200
    for _ in range(3):journal.append(d.store,owner,record())
    first=journal.overview(d.store,owner,limit=2)
    second=journal.overview(d.store,owner,offset=first['next_offset'],limit=2)
    assert len(first['records'])==2 and len(second['records'])==1 and second['next_offset'] is None
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','production')
    assert request(d,'/api/farm/setup',raw=raw)[0]==403


def test_browser_setup_and_stable_stock_entry(dashboard):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url)
            expect(page.locator('#chiefAgents')).to_contain_text('Farm Agent')
            page.locator('nav .domainNav[data-domain="farming"] > summary').click()
            page.get_by_role('button',name='Poultry records',exact=True).click()
            page.get_by_role('button', name='Expand all sections', exact=True).click()
            expect(page.locator('#farmSetupCard')).to_be_visible()
            page.select_option('#farmEntityType','FEED_STORE')
            page.fill('#farmEntityName','Browser store');page.fill('#farmEntityDate','2025-01-01')
            page.get_by_role('button',name='Create Farm location',exact=True).click()
            expect(page.locator('#farmSetupResult')).to_have_text('Saved.')
            expect(page.locator('#farmEntity option')).to_have_count(2)
            page.select_option('#farmEntity',index=1)
            page.select_option('#farmKind','feed_opening');page.fill('#farmQuantity','12.125')
            page.get_by_role('button',name='Save record',exact=True).click()
            expect(page.locator('#farmResult')).to_have_text('Record saved.')
            expect(page.locator('#farmBalances')).to_contain_text('12.125')
        finally:browser.close()
