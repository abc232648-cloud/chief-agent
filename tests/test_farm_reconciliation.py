import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from domains.farming import reconciliation as counts, setup, journal
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_setup import entity
from tests.test_farm_journal import record
from tests.test_identity_http import request


def count(e, **changes):
    return {'event_id':str(uuid.uuid4()),'operation':'COUNT','entity_id':e['entity_id'],'unit':'kg','quantity':'9.500','observed_at':'2026-01-02T12:00:00Z','reason':'Physical measurement',**changes}


def decide(p, operation='APPROVE'):
    return {'event_id':str(uuid.uuid4()),'operation':operation,'reference':p['event_id'],'reason':'Reviewed physical count'}


def stock(d, opening=True):
    e=entity();setup.append(d.store,d.credentials['principal'],e)
    if opening:journal.append(d.store,d.credentials['principal'],record(kind='feed_opening',entity_id=e['entity_id'],location=e['name'],quantity='10.000'))
    return e


def test_worker_count_owner_approval_preserves_stock_history(dashboard):
    d=dashboard;owner=d.credentials['principal'];e=stock(d);service=IdentityService(d.store)
    service.create_user(owner,'stock-counter',PASSWORD,'Worker',('farming',));_,worker=service.login('stock-counter',PASSWORD)
    p=count(e);result=counts.append(d.store,worker,p)
    assert result['record']['difference']=='-0.500'
    assert journal.overview(d.store,owner)['balances'][0]['recorded_balance']=='10.000'
    with pytest.raises(PermissionError):counts.append(d.store,worker,decide(p))
    decision=decide(p);counts.append(d.store,owner,decision)
    assert counts.append(d.store,owner,decision)['status']=='ALREADY_RECORDED'
    view=journal.overview(d.store,owner)
    assert view['balances'][0]['recorded_balance']=='9.500'
    assert len(view['records'])==2
    original=next(r for r in view['records'] if r['payload']['kind']=='feed_opening')
    assert original['payload']['quantity']=='10.000'
    adjustment=next(r for r in view['records'] if '_adjustment_' in r['payload']['kind'])
    with pytest.raises(PermissionError):journal.append(d.store,owner,{**adjustment['payload'],'event_id':str(uuid.uuid4()),'corrects':adjustment['payload']['event_id'],'reason':'Attempted bypass'})
    with pytest.raises(ValueError):counts.append(d.store,owner,decide(p))


def test_unknown_opening_and_stale_history_fail_closed(dashboard):
    d=dashboard;owner=d.credentials['principal'];e=stock(d,False)
    p=count(e);result=counts.append(d.store,owner,p)
    assert result['record']['recorded_balance'] is None and result['record']['difference'] is None
    with pytest.raises(ValueError,match='unknown'):counts.append(d.store,owner,decide(p))
    journal.append(d.store,owner,record(kind='feed_opening',entity_id=e['entity_id'],location=e['name'],quantity='10.000'))
    fresh=count(e);counts.append(d.store,owner,fresh)
    journal.append(d.store,owner,record(entity_id=e['entity_id'],location=e['name'],quantity='1',observed_at='2026-01-03T12:00:00Z'))
    with pytest.raises(ValueError,match='changed'):counts.append(d.store,owner,decide(fresh))
    assert journal.overview(d.store,owner)['balances'][0]['recorded_balance']=='11.000'


def test_count_visibility_units_and_no_schema_changes(dashboard):
    d=dashboard;owner=d.credentials['principal'];e=stock(d);service=IdentityService(d.store)
    service.create_user(owner,'other-counter',PASSWORD,'Worker',('farming',));_,worker=service.login('other-counter',PASSWORD)
    with d.store._connect() as con:before=list(map(tuple,con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))
    counts.append(d.store,owner,count(e))
    assert counts.overview(d.store,worker)['items']==[]
    with pytest.raises(ValueError):counts.append(d.store,owner,count(e,unit='birds'))
    with pytest.raises(ValueError):counts.append(d.store,owner,count(e,quantity=''))
    service.create_user(owner,'job-counter',PASSWORD,'Manager',('jobs',));raw,_=service.login('job-counter',PASSWORD)
    assert request(d,'/api/farm/physical-counts',raw=raw)[0]==403
    with d.store._connect() as con:assert before==list(map(tuple,con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))


def test_indexed_count_adjustment_after_verified_restore(dashboard):
    from tests.test_farm_indexes import upgrade
    d=dashboard;owner=d.credentials['principal'];e=stock(d);upgrade(d)
    p=count(e,quantity='12.125');counts.append(d.store,owner,p);counts.append(d.store,owner,decide(p))
    assert journal.overview(d.store,owner)['balances'][0]['recorded_balance']=='12.125'


def test_interrupted_approval_rolls_back_adjustment_and_retries(dashboard,monkeypatch):
    d=dashboard;owner=d.credentials['principal'];e=stock(d);p=count(e)
    counts.append(d.store,owner,p);decision=decide(p);original=IdentityService._event
    def fail(self,con,principal,operation,*args,**kwargs):
        if operation=='FARM_PHYSICAL_COUNT_APPROVE':raise RuntimeError('Synthetic audit interruption')
        return original(self,con,principal,operation,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(IdentityService,'_event',fail)
        with pytest.raises(RuntimeError):counts.append(d.store,owner,decision)
    assert journal.overview(d.store,owner)['balances'][0]['recorded_balance']=='10.000'
    assert counts.overview(d.store,owner)['items'][0]['state']=='PENDING'
    counts.append(d.store,owner,decision)
    assert journal.overview(d.store,owner)['balances'][0]['recorded_balance']=='9.500'


def test_simultaneous_approvals_cannot_double_adjust(dashboard):
    d=dashboard;owner=d.credentials['principal'];e=stock(d);p=count(e);counts.append(d.store,owner,p)
    def approve(_):
        try:return counts.append(d.store,owner,decide(p))['status']
        except ValueError as exc:
            assert 'already decided' in str(exc)
            return 'REJECTED'
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(approve,range(2)))==['RECORDED','REJECTED']
    assert journal.overview(d.store,owner)['balances'][0]['recorded_balance']=='9.500'


def test_physical_history_protects_dates_and_invalid_units_are_rejected(dashboard):
    from tests.test_farm_setup_history import update
    d=dashboard;owner=d.credentials['principal'];e=stock(d,False)
    counts.append(d.store,owner,count(e))
    with pytest.raises(ValueError,match='history'):setup.append(d.store,owner,update(e,opened_on='2026-03-01'))
    assert request(d,'/api/farm/physical-counts','POST',count(e,unit=[]),d.credentials['raw'])[0]==400


def test_browser_count_and_explicit_owner_approval(dashboard):
    from playwright.sync_api import sync_playwright,expect
    d=dashboard;stock(d)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work')
            page.get_by_role('button',name='Expand all sections',exact=True).click()
            page.select_option('#farmPhysicalEntity',index=1);page.select_option('#farmPhysicalUnit','kg')
            page.fill('#farmPhysicalQuantity','9.500');page.fill('#farmPhysicalReason','Counted the store')
            page.get_by_role('button',name='Save physical count for review',exact=True).click()
            expect(page.locator('#farmPhysicalCountResult')).to_contain_text('Stock is unchanged')
            page.get_by_label('Owner decision reason').fill('Reviewed measurement')
            page.get_by_role('button',name='Approve stock adjustment',exact=True).click()
            expect(page.locator('#farmPhysicalItems')).to_contain_text('Approved')
            expect(page.locator('#farmBalances')).to_contain_text('9.500')
        finally:browser.close()
