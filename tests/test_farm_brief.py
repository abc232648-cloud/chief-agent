"""Daily evidence, schedule authorization, calendar boundaries and UI delivery."""
from datetime import datetime, timezone
import json
import uuid
import pytest
from domains.farming import brief, journal, setup, bookkeeping
from tests.test_farm_journal import record
from tests.test_farm_setup import entity
from tests.test_farm_staff import people
from tests.test_farm_bookkeeping import entry
from tests.test_identity_http import request
from playwright.sync_api import sync_playwright, expect


def schedule(d,owner,uid,monkeypatch):
    store=entity();setup.append(d.store,owner,store)
    now=datetime(2026,1,2,12,tzinfo=timezone.utc)
    monkeypatch.setattr(brief,'utc_now',lambda:now)
    p={'event_id':str(uuid.uuid4()),'expected_revision':None,'requirements':[{'entity_id':store['entity_id'],'kind':'feed_used','due_time':'13:00','assignee':uid}]}
    return store,p


def test_owner_schedule_idempotency_conflict_and_nonretroactive_deadlines(dashboard,monkeypatch):
    d=dashboard;owner,uid,worker,_,other=people(d)
    store,p=schedule(d,owner,uid,monkeypatch)
    with pytest.raises(PermissionError):brief.configure(d.store,worker,p)
    assert brief.configure(d.store,owner,p)['effective_on']=='2026-01-03'
    assert brief.configure(d.store,owner,p)['status']=='ALREADY_RECORDED'
    with pytest.raises(ValueError):brief.configure(d.store,owner,{**p,'requirements':[]})
    with pytest.raises(ValueError):brief.configure(d.store,owner,{**p,'event_id':str(uuid.uuid4())})
    assert brief.overview(d.store,owner)['reporting']==[]
    monkeypatch.setattr(brief,'utc_now',lambda:datetime(2026,1,3,11,59,tzinfo=timezone.utc))
    assert brief.overview(d.store,worker)['reporting'][0]['status']=='AWAITING'
    monkeypatch.setattr(brief,'utc_now',lambda:datetime(2026,1,3,12,tzinfo=timezone.utc))
    view=brief.overview(d.store,worker)
    assert view['reporting'][0]['status']=='MISSING' and len(view['alerts'])==1
    assert 'finance' not in view and view['settings']=={'can_configure':False}
    assert brief.overview(d.store,other)['reporting']==[]
    journal.append(d.store,owner,record(kind='feed_used',entity_id=store['entity_id'],location=store['name'],quantity='0',observed_at='2026-01-03T11:00:00Z'))
    view=brief.overview(d.store,worker)
    assert view['reporting'][0]['status']=='REPORTED_LATE' and view['alerts']==[]
    assert view['metrics']==[] # Other people's report quantities remain private.
    assert brief.overview(d.store,owner)['metrics'][0]['quantity']=='0'


def test_brief_uses_effective_observation_day_exact_decimal_and_not_history_page(dashboard):
    d=dashboard;p=d.credentials['principal']
    first=record(kind='feed_used',quantity='1.125',observed_at='2026-01-01T23:30:00Z')
    journal.append(d.store,p,first)
    journal.append(d.store,p,record(kind='feed_used',quantity='2.250',observed_at=first['observed_at'],corrects=first['event_id'],reason='Corrected weighing'))
    # Populate more than the history page through append-only test records.
    with d.store._connect() as con:
        template=json.loads(con.execute("SELECT data_json FROM domain_records WHERE kind=? ORDER BY id DESC LIMIT 1",(journal.KIND,)).fetchone()[0])
        for _ in range(105):
            template['payload'].update(event_id=str(uuid.uuid4()),corrects=None,reason='',quantity='0.001')
            con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',('farming',journal.KIND,json.dumps(template),1))
    result=brief.overview(d.store,p,'2026-01-02')
    assert result['metrics'][0]['quantity']=='2.355' and result['metrics'][0]['reports']==106
    assert brief.overview(d.store,p,'2026-01-01')['metrics']==[]
    assert 'finance' in result and result['external_notifications']=='NOT_CONFIGURED'
    with pytest.raises(ValueError):brief.overview(d.store,p,'2099-01-01')


def test_schedule_rejects_invalid_or_duplicate_requirements(dashboard,monkeypatch):
    d=dashboard;owner,uid,_,_,_=people(d);_,p=schedule(d,owner,uid,monkeypatch)
    for field,value in [('due_time','24:00'),('kind','birds_opening'),('assignee','missing-user')]:
        with pytest.raises(ValueError):brief.configure(d.store,owner,{**p,'requirements':[{**p['requirements'][0],field:value}]})
    with pytest.raises(ValueError):brief.configure(d.store,owner,{**p,'requirements':p['requirements']*2})


def test_daily_brief_phone_and_laptop(dashboard):
    d=dashboard
    bookkeeping.append(d.store,d.credentials['principal'],entry(counterparty='PRIVATE_BRIEF_BUYER'))
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            for width in (390,1366):
                page=browser.new_page(viewport={'width':width,'height':850});page.goto(d.url+'/work')
                page.locator('#farmBriefCard > summary').click()
                expect(page.locator('#farmBriefContent')).to_contain_text('No observations recorded')
                expect(page.locator('#farmBriefSettings')).to_contain_text('Owner: reporting schedule')
                page.get_by_role('button',name='Save schedule for tomorrow',exact=True).click()
                expect(page.locator('#farmBriefScheduleResult')).to_contain_text('Saved. Schedule takes effect')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.close()
        finally:browser.close()


def test_brief_http_scope_and_schedule_csrf(dashboard):
    from identity.service import IdentityService
    from tests.checkpoint_f_fixture import PASSWORD
    from tests.test_farm_setup import assign
    d=dashboard;s=IdentityService(d.store);owner=d.credentials['principal']
    s.create_user(owner,'brief-job',PASSWORD,'Worker',('jobs',));job,_=s.login('brief-job',PASSWORD)
    uid=s.create_user(owner,'brief-supervisor',PASSWORD,'Worker',('farming',));supervisor,_=s.login('brief-supervisor',PASSWORD)
    assign(d.store,owner,uid,'SUPERVISOR')
    assert request(d,'/api/farm/brief')[0]==401
    assert request(d,'/api/farm/brief',raw=job)[0]==403
    status,_,body=request(d,'/api/farm/brief',raw=supervisor)
    assert status==200 and 'finance' not in body and body['settings']=={'can_configure':False}
    payload={'event_id':str(uuid.uuid4()),'expected_revision':None,'requirements':[]}
    assert request(d,'/api/farm/brief/schedule','POST',payload,supervisor)[0]==403
    assert request(d,'/api/farm/brief/schedule','POST',payload,d.credentials['raw'],csrf=False)[0]==403
    assert request(d,'/api/farm/brief/schedule','POST',payload,d.credentials['raw'],extra={'Origin':'https://untrusted.invalid'})[0]==403
    with d.store._connect() as con:assert brief.schedules(con)==[]


def test_later_correction_does_not_make_original_report_late(dashboard,monkeypatch):
    d=dashboard;owner,uid,_,_,_=people(d);store,p=schedule(d,owner,uid,monkeypatch)
    brief.configure(d.store,owner,p)
    original=record(kind='feed_used',entity_id=store['entity_id'],location=store['name'],quantity='1',observed_at='2026-01-03T10:00:00Z')
    monkeypatch.setattr(journal,'utc_now',lambda:datetime(2026,1,3,11,tzinfo=timezone.utc))
    journal.append(d.store,owner,original)
    monkeypatch.setattr(journal,'utc_now',lambda:datetime(2026,1,3,14,tzinfo=timezone.utc))
    journal.append(d.store,owner,record(kind='feed_used',entity_id=store['entity_id'],location=store['name'],quantity='2',observed_at=original['observed_at'],corrects=original['event_id'],reason='Reweighed'))
    monkeypatch.setattr(brief,'utc_now',lambda:datetime(2026,1,3,15,tzinfo=timezone.utc))
    view=brief.overview(d.store,owner)
    assert view['reporting'][0]['status']=='REPORTED' and view['metrics'][0]['quantity']=='2'
