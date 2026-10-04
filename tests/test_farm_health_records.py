import uuid
import pytest
from domains.farming import health_records as health, setup
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_setup import entity
from tests.test_identity_http import request


def flock(d):
    owner=d.credentials['principal']
    house={**entity(),'entity_type':'HOUSE','name':'House A'}
    setup.append(d.store,owner,house)
    f={**entity(),'entity_type':'FLOCK','name':'Flock A','house_id':house['entity_id']}
    setup.append(d.store,owner,f)
    return f['entity_id']


def record(fid,**kw):
    return dict(event_id=str(uuid.uuid4()),kind='OBSERVATION',entity_id=fid,observed_at='2026-01-01T08:00:00+01:00',summary='Observed flock',product='',quantity=None,unit='',administered_by='',batch='',expires_on=None,source_reference='',instructions='',instruction_source='',corrects=None,reason='',**kw)


def test_history_unknowns_retry_and_no_schema_change(dashboard):
    d=dashboard;p=d.credentials['principal'];fid=flock(d)
    with d.store._connect() as con:before=list(map(tuple,con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))
    r={**record(fid),'kind':'VACCINATION','product':'Recorded product','administered_by':'Reported veterinarian'}
    assert health.append(d.store,p,r)['status']=='RECORDED'
    assert health.append(d.store,p,r)['status']=='ALREADY_RECORDED'
    item=health.overview(d.store,p)['items'][0]
    assert item['payload']['quantity'] is None and item['payload']['instructions']==''
    assert item['payload']['observed_at']=='2026-01-01T07:00:00.000000Z'
    assert item['evidence_status']=='HUMAN_REPORTED'
    with pytest.raises(ValueError):health.append(d.store,p,{**r,'summary':'Changed retry'})
    with d.store._connect() as con:assert before==list(map(tuple,con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))


@pytest.mark.parametrize('changes',[{'kind':[]},{'observed_at':'2026-01-01T08:00:00'},{'observed_at':'2099-01-01T08:00:00Z'},{'instructions':'Some instruction'},{'quantity':'2','unit':'ml'},{'summary':''},{'expires_on':'invalid'}])
def test_invalid_records_rejected(dashboard,changes):
    d=dashboard;fid=flock(d)
    with pytest.raises(ValueError):health.append(d.store,d.credentials['principal'],{**record(fid),**changes})
    assert health.overview(d.store,d.credentials['principal'])['total']==0


def test_worker_privacy_correction_chain_and_cross_domain(dashboard):
    d=dashboard;p=d.credentials['principal'];fid=flock(d);svc=IdentityService(d.store)
    workers=[]
    for name,domain in [('worker-a','farming'),('worker-b','farming'),('job-worker','jobs')]:
        svc.create_user(p,name,PASSWORD,'Worker',(domain,));workers.append(svc.login(name,PASSWORD)[1])
    r=record(fid);health.append(d.store,workers[0],r)
    assert health.overview(d.store,workers[1])['total']==0
    corrected={**r,'event_id':str(uuid.uuid4()),'corrects':r['event_id'],'reason':'Corrected description','summary':'Corrected observation'}
    with pytest.raises(PermissionError):health.append(d.store,workers[0],corrected)
    health.append(d.store,p,corrected)
    assert [x['is_current'] for x in health.overview(d.store,workers[0])['items']]==[True,False]
    with pytest.raises(ValueError):health.append(d.store,p,{**corrected,'event_id':str(uuid.uuid4())})
    with pytest.raises(PermissionError):health.overview(d.store,workers[2])


def test_expired_actual_treatment_warning_and_audit_atomicity(dashboard,monkeypatch):
    d=dashboard;p=d.credentials['principal'];fid=flock(d)
    r={**record(fid),'kind':'MEDICINE','product':'Recorded product','quantity':'2.125','unit':'ml','administered_by':'Vet','expires_on':'2025-12-01'}
    health.append(d.store,p,r)
    assert health.overview(d.store,p)['items'][0]['expired_at_administration'] is True
    def fail(*a,**kw):raise RuntimeError('Synthetic audit failure')
    monkeypatch.setattr(IdentityService,'_event',fail)
    with pytest.raises(RuntimeError):health.append(d.store,p,record(fid))
    assert health.overview(d.store,p)['total']==1


def test_http_and_browser_entry_correction(dashboard):
    from playwright.sync_api import sync_playwright,expect
    d=dashboard;flock(d)
    assert request(d,'/api/farm/health-records',raw=d.credentials['raw'])[0]==200
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work');page.get_by_role('button',name='Expand all sections',exact=True).click()
            page.fill('#farmHealth_observed_at','2026-01-01T08:00');page.fill('#farmHealth_summary','Flock checked')
            page.get_by_role('button',name='Save health record',exact=True).click()
            expect(page.locator('#farmHealthStatus')).to_contain_text('Health history saved')
            page.get_by_role('button',name='Correct health record',exact=True).click()
            page.fill('#farmHealth_summary','Flock and water checked');page.fill('#farmHealth_reason','Added omitted detail')
            page.get_by_role('button',name='Save health correction',exact=True).click()
            expect(page.locator('#farmHealthHistory')).to_contain_text('Flock and water checked')
            assert health.overview(d.store,d.credentials['principal'])['total']==2
        finally:browser.close()
