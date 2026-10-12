import uuid
import pytest
from domains.farming import egg_loss_links as links, journal, costing
from tests.test_farm_journal import record
from tests.test_identity_http import request
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD


def seed(d):
    p=d.credentials['principal']
    collection=record(kind='eggs_collected',quantity='100',location='Egg store')
    loss=record(kind='eggs_lost',quantity='10',location='Egg store',observed_at='2026-01-02T08:00:00Z')
    for r in (collection,loss):journal.append(d.store,p,r)
    return collection,loss


def command(c,l,eggs=10,**kw):
    return dict(event_id=str(uuid.uuid4()),loss_id=l['event_id'],expected_revision=None,
                allocations=[{'collection_id':c['event_id'],'eggs':eggs}],reason='Checked collection register',**kw)


def report(d):
    return costing.report(d.store,d.credentials['principal'],start='2026-01-01',end='2026-01-01',currency='NGN',feed_minor_per_kg=100)


def test_link_updates_costing_without_stock_or_schema_change(dashboard):
    d=dashboard;p=d.credentials['principal'];c,l=seed(d)
    journal.append(d.store,p,record(kind='feed_used',quantity='9'))
    with d.store._connect() as con:
        before=journal.read_rows(con);schema=list(map(tuple,con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))
    r=report(d);assert r['saleable_eggs'] is None and r['unallocated_relevant_losses']==10
    cmd=command(c,l);assert links.append(d.store,p,cmd)['status']=='RECORDED'
    assert links.append(d.store,p,cmd)['status']=='ALREADY_RECORDED'
    r=report(d);assert r['saleable_eggs']==90 and r['saleable_cost_per_egg_minor']=='10.0000'
    assert cmd['event_id'] in r['source_references'] and l['event_id'] in r['source_references']
    with d.store._connect() as con:
        assert journal.read_rows(con)==before
        assert list(map(tuple,con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))==schema


def test_partial_unknown_and_revision_conflicts(dashboard):
    d=dashboard;p=d.credentials['principal'];c,l=seed(d);cmd=command(c,l,4);links.append(d.store,p,cmd)
    assert report(d)['unallocated_relevant_losses']==6 and report(d)['saleable_eggs'] is None
    with pytest.raises(ValueError,match='changed'):links.append(d.store,p,command(c,l))
    cleared={**command(c,l),'expected_revision':cmd['event_id'],'allocations':[]};links.append(d.store,p,cleared)
    assert report(d)['unallocated_relevant_losses']==10
    assert len(links.overview(d.store,p)['history'])==2


@pytest.mark.parametrize('count',[0,-1,True,1.5,11])
def test_invalid_counts(dashboard,count):
    d=dashboard;c,l=seed(d)
    with pytest.raises(ValueError):links.append(d.store,d.credentials['principal'],command(c,l,count))


def test_corrections_invalidate_links_without_rewriting_history(dashboard):
    d=dashboard;p=d.credentials['principal'];c,l=seed(d);cmd=command(c,l);links.append(d.store,p,cmd)
    journal.append(d.store,p,{**c,'event_id':str(uuid.uuid4()),'corrects':c['event_id'],'quantity':'95','reason':'Correct count'})
    assert links.overview(d.store,p)['items'][0]['needs_review']
    assert report(d)['saleable_eggs'] is None
    assert links.overview(d.store,p)['history'][0]['payload']==cmd


def test_shared_collection_cannot_be_overallocated(dashboard):
    d=dashboard;p=d.credentials['principal'];c,l=seed(d)
    other={**l,'event_id':str(uuid.uuid4()),'quantity':'100'};journal.append(d.store,p,other)
    links.append(d.store,p,command(c,l))
    with pytest.raises(ValueError,match='exceed'):links.append(d.store,p,command(c,other,100))


def test_stock_and_time_mismatch(dashboard):
    d=dashboard;p=d.credentials['principal'];c,l=seed(d)
    for change in ({'location':'Other store'},{'observed_at':'2026-01-03T08:00:00Z'}):
        other={**c,**change,'event_id':str(uuid.uuid4())};journal.append(d.store,p,other)
        with pytest.raises(ValueError):links.append(d.store,p,command(other,l))


def test_permissions_csrf_and_browser(dashboard):
    from playwright.sync_api import sync_playwright,expect
    d=dashboard;p=d.credentials['principal'];c,l=seed(d);svc=IdentityService(d.store)
    for role,domains in [('Worker',('farming',)),('Manager',('farming',)),('Owner',('jobs',))]:
        name='links-'+role;svc.create_user(p,name,PASSWORD,role,domains);_,other=svc.login(name,PASSWORD)
        with pytest.raises(PermissionError):links.append(d.store,other,command(c,l))
    assert request(d,'/api/farm/egg-loss-links',raw='bad')[0]==401
    assert request(d,'/api/farm/egg-loss-links','POST',command(c,l),raw=d.credentials['raw'],csrf=False)[0]==403
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work');page.get_by_role('button',name='Expand all sections',exact=True).click()
            host=page.locator('#farmEggLossLinks');host.get_by_role('button',name='Add collection link',exact=True).click()
            host.get_by_label('Collection record',exact=True).select_option(c['event_id'])
            host.get_by_label('Eggs linked to this collection',exact=True).fill('10')
            host.get_by_label('Reason for loss links',exact=True).fill('Checked daily register')
            host.get_by_role('button',name='Save loss links',exact=True).click()
            expect(host.get_by_role('status')).to_contain_text('Stock unchanged')
            assert report(d)['saleable_eggs']==90
        finally:browser.close()
