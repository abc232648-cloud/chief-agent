import uuid
import pytest
from domains.farming import setup, journal
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_setup import entity
from tests.test_farm_journal import record


def update(item, **changes):
    return dict(event_id=str(uuid.uuid4()), operation='update_entity',
                entity_id=item['entity_id'], expected_revision=item.get('revision', item['event_id']),
                name=changes.get('name', item['name']), opened_on=changes.get('opened_on', item['opened_on']),
                reason='Correcting synthetic setup')


def test_unknown_date_rename_retry_and_original_correction(dashboard):
    d=dashboard; owner=d.credentials['principal']; e={**entity(), 'opened_on':None}
    setup.append(d.store,owner,e)
    original=record(kind='feed_opening',entity_id=e['entity_id'],location=e['name'],quantity='12')
    journal.append(d.store,owner,original)
    with d.store._connect() as con:
        before=list(map(tuple,con.execute('SELECT id,data_json FROM domain_records ORDER BY id')))
    change=update(e,name='Renamed store')
    setup.append(d.store,owner,change)
    assert setup.append(d.store,owner,change)['status']=='ALREADY_RECORDED'
    assert journal.append(d.store,owner,original)['status']=='ALREADY_RECORDED'
    correction={**original,'event_id':str(uuid.uuid4()),'corrects':original['event_id'],'quantity':'15','reason':'Count corrected'}
    journal.append(d.store,owner,correction)
    assert journal.overview(d.store,owner)['balances'][0]['recorded_balance']=='15'
    assert journal.overview(d.store,owner)['balances'][0]['location']=='Renamed store'
    with d.store._connect() as con:
        assert setup.entities(con)[e['entity_id']]['name']=='Renamed store'
        assert setup.entities(con)[e['entity_id']]['opened_on'] is None
        assert list(map(tuple,con.execute('SELECT id,data_json FROM domain_records ORDER BY id')))[:len(before)]==before
    with pytest.raises(ValueError,match='Location must match'):
        journal.append(d.store,owner,record(entity_id=e['entity_id'],location=e['name'],kind='feed_used'))


def test_update_rejects_stale_revision_and_incompatible_history(dashboard):
    d=dashboard; owner=d.credentials['principal']; e={**entity(),'opened_on':None}
    setup.append(d.store,owner,e)
    journal.append(d.store,owner,record(entity_id=e['entity_id'],location=e['name'],kind='feed_opening',observed_at='2025-02-01T12:00:00+01:00'))
    with pytest.raises(ValueError,match='history'):
        setup.append(d.store,owner,update(e,opened_on='2025-03-01'))
    setup.append(d.store,owner,update(e,opened_on='2025-01-01'))
    with pytest.raises(ValueError,match='refresh'):
        setup.append(d.store,owner,update(e,name='Stale edit'))


def test_unknown_house_dates_and_backfill_parent_constraints(dashboard):
    d=dashboard; owner=d.credentials['principal']; h={**entity(),'entity_type':'HOUSE','opened_on':None}
    setup.append(d.store,owner,h)
    f={**entity(),'entity_type':'FLOCK','house_id':h['entity_id'],'opened_on':None}
    setup.append(d.store,owner,f)
    setup.append(d.store,owner,update(f,opened_on='2025-02-01'))
    with pytest.raises(ValueError,match='House opening'):
        setup.append(d.store,owner,update(h,opened_on='2025-03-01'))
    setup.append(d.store,owner,update(h,opened_on='2025-01-01'))


def test_worker_cannot_rename_and_unknown_opening_is_not_stock(dashboard):
    d=dashboard; owner=d.credentials['principal']; e={**entity(),'opened_on':None}
    setup.append(d.store,owner,e)
    service=IdentityService(d.store); service.create_user(owner,'setup-worker',PASSWORD,'Worker',('farming',))
    _,worker=service.login('setup-worker',PASSWORD)
    with pytest.raises(PermissionError): setup.append(d.store,worker,update(e,name='Forbidden'))
    assert journal.overview(d.store,owner)['balances']==[]
    assert 'entity_history' not in setup.overview(d.store,worker)


def test_indexed_rename_matches_legacy_after_verified_restore(dashboard):
    from tests.test_farm_indexes import upgrade
    d=dashboard;owner=d.credentials['principal'];e={**entity(),'opened_on':None}
    setup.append(d.store,owner,e)
    original=record(kind='feed_opening',entity_id=e['entity_id'],location=e['name'],quantity='2.500')
    journal.append(d.store,owner,original)
    setup.append(d.store,owner,update(e,name='Current label'))
    before=journal.overview(d.store,owner)
    upgrade(d)  # Existing B.5 backup and quarantined restore prerequisite.
    assert journal.overview(d.store,owner)==before
    assert journal.append(d.store,owner,original)['status']=='ALREADY_RECORDED'
    assert before['balances'][0]['location']=='Current label'
    assert before['records'][0]['payload']['location']==e['name']
    history=setup.overview(d.store,owner)['entity_history']
    assert len(history)==2 and history[0]['payload']['name']=='Current label'


def test_browser_unknown_date_and_rename(dashboard):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(); page.goto(dashboard.url+'/work')
            page.get_by_role('button',name='Expand all sections',exact=True).click()
            page.select_option('#farmEntityType','FEED_STORE')
            page.fill('#farmEntityName','Provisional store')
            page.get_by_role('button',name='Create Farm location',exact=True).click()
            expect(page.locator('#farmSetupResult')).to_have_text('Saved.')
            page.select_option('#farmEntityUpdateId',index=1)
            expect(page.locator('#farmEntityUpdateDate')).to_have_value('')
            page.fill('#farmEntityUpdateName','Confirmed store')
            page.fill('#farmEntityUpdateReason','Name confirmed')
            page.get_by_role('button',name='Update Farm location',exact=True).click()
            expect(page.locator('#farmEntityUpdateResult')).to_have_text('Saved.')
            expect(page.locator('#farmEntity')).to_contain_text('Confirmed store')
            expect(page.locator('#farmEntityHistory')).to_contain_text('Name confirmed')
        finally:
            browser.close()
