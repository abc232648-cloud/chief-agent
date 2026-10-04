import time
from datetime import datetime, timezone
import pytest
from control.schedules import create, deliver, change, list_schedules
from control.agents import AgentControls
from application.composition import default_registry
from test_chief_controls import request


def payload(now):
    return {'domain':'farming','action':'in_app_reminder','title':'Check water level',
            'first_run':datetime.fromtimestamp(now+60,timezone.utc).isoformat(),
            'timezone':'Africa/Lagos','interval_minutes':10}


def test_api_and_atomic_no_replay(dashboard):
    now=time.time();code,_,row=request(dashboard,'/api/general-schedules','POST',payload(now))
    assert code==200
    controls=AgentControls(dashboard.store,default_registry())
    assert deliver(dashboard.store,controls,now=now+61)==1
    assert deliver(dashboard.store,controls,now=now+61)==0
    assert deliver(dashboard.store,controls,now=now+6000)==1
    assert deliver(dashboard.store,controls,now=now+6000)==0
    current=list_schedules(dashboard.store)[0]
    assert current['next_due']>now+6000
    with pytest.raises(ValueError):change(dashboard.store,row['id'],{'enabled':False,'revision':row['revision']})
    change(dashboard.store,row['id'],{'enabled':False,'revision':current['revision']})
    assert deliver(dashboard.store,controls,now=now+12000)==0


def test_unknown_actions_rejected(dashboard):
    body=payload(time.time());body['action']='camera_snapshot'
    assert request(dashboard,'/api/general-schedules','POST',body)[0]==400


def test_disabled_creator_stops_schedule(dashboard):
    now=time.time();row=request(dashboard,'/api/general-schedules','POST',payload(now))[2]
    with dashboard.store._connect() as con:con.execute('UPDATE human_identities SET enabled=0 WHERE id=?',(row['actor'],))
    assert deliver(dashboard.store,AgentControls(dashboard.store,default_registry()),now=now+61)==0
    assert list_schedules(dashboard.store)[0]['status']=='AUTHORITY_REVOKED'


def test_one_time_schedule_not_replayed(dashboard):
    now=time.time();body=payload(now);body['interval_minutes']=0
    row=request(dashboard,'/api/general-schedules','POST',body)[2]
    controls=AgentControls(dashboard.store,default_registry())
    assert deliver(dashboard.store,controls,now=now+61)==1
    assert deliver(dashboard.store,controls,now=now+10000)==0
    current=list_schedules(dashboard.store)[0]
    with pytest.raises(ValueError):change(dashboard.store,row['id'],{'enabled':True,'revision':current['revision']})


def test_closed_schedule_cannot_be_reopened_by_pause(dashboard):
    now=time.time();body=payload(now);body['interval_minutes']=0
    row=request(dashboard,'/api/general-schedules','POST',body)[2]
    deliver(dashboard.store,AgentControls(dashboard.store,default_registry()),now=now+61)
    current=list_schedules(dashboard.store)[0]
    with pytest.raises(ValueError):
        change(dashboard.store,row['id'],{'enabled':False,'revision':current['revision']})
    assert list_schedules(dashboard.store)[0]['status']=='COMPLETED'


def test_failed_notification_rolls_back_and_retries_once(dashboard):
    import sqlite3
    from control.notifications import initialize
    initialize(dashboard.store)
    now=time.time();request(dashboard,'/api/general-schedules','POST',payload(now))
    controls=AgentControls(dashboard.store,default_registry())
    with dashboard.store._connect() as con:
        con.execute("CREATE TRIGGER reject_test_notice BEFORE INSERT ON notifications BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    with pytest.raises(sqlite3.IntegrityError):deliver(dashboard.store,controls,now=now+61)
    assert list_schedules(dashboard.store)[0]['last_run'] is None
    with dashboard.store._connect() as con:con.execute('DROP TRIGGER reject_test_notice')
    assert deliver(dashboard.store,controls,now=now+61)==1
    assert deliver(dashboard.store,controls,now=now+61)==0


def test_concurrent_delivery_creates_one_notice(dashboard):
    from concurrent.futures import ThreadPoolExecutor
    from control.notifications import initialize
    initialize(dashboard.store)
    now=time.time();request(dashboard,'/api/general-schedules','POST',payload(now))
    controls=AgentControls(dashboard.store,default_registry())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:deliver(dashboard.store,controls,now=now+61),range(2)))
    assert sorted(results)==[0,1]
    with dashboard.store._connect() as con:
        assert con.execute("SELECT count(*) FROM notifications WHERE title='Scheduled reminder'").fetchone()[0]==1


@pytest.mark.parametrize("width",[390,1280])
def test_browser_create_and_pause_reminder(dashboard,width):
    from playwright.sync_api import sync_playwright, expect
    from browser_navigation import navigate
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':width,'height':850});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('dialog',lambda d:d.accept())
        page.goto(dashboard.url)
        expect(page.locator('nav button[data-target="scheduler"]')).to_have_count(1)
        navigate(page,'scheduler')
        page.locator('#generalDomain').select_option('farming')
        page.locator('#generalTitle').fill('Synthetic flock check')
        page.locator('#generalFirst').fill(datetime.fromtimestamp(time.time()+3600).strftime('%Y-%m-%dT%H:%M'))
        page.locator('[data-general-action="create"]').click()
        card=page.locator('#generalSchedules article').filter(has_text='Synthetic flock check')
        expect(card).to_contain_text('SCHEDULED')
        card.get_by_role('button',name='Pause',exact=True).click()
        expect(card).to_contain_text('PAUSED')
        card.get_by_role('button',name='Resume',exact=True).click()
        expect(card).to_contain_text('SCHEDULED')
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert not errors
        browser.close()


@pytest.mark.parametrize('change',[{'csrf':False},{'origin':False},{'extra':{'Origin':'https://attacker.invalid'}},{'extra':{'Host':'attacker.invalid'}}])
def test_schedule_request_security(dashboard,change):
    from test_identity_http import request as raw_request
    assert raw_request(dashboard,'/api/general-schedules','POST',payload(time.time()),dashboard.credentials['raw'],**change)[0]==403
    assert list_schedules(dashboard.store)==[]


def test_manager_cannot_create_installation_schedule(dashboard):
    from test_identity_http import request as raw_request
    from identity.service import IdentityService
    from tests.checkpoint_f_fixture import PASSWORD
    service=IdentityService(dashboard.store)
    service.create_user(dashboard.credentials['principal'],'schedule-manager',PASSWORD,'Manager',('farming',))
    raw,_=service.login('schedule-manager',PASSWORD)
    assert raw_request(dashboard,'/api/general-schedules','POST',payload(time.time()),raw)[0]==403
    assert list_schedules(dashboard.store)==[]


def test_invalid_domain_type_is_client_error(dashboard):
    body=payload(time.time());body['domain']=[]
    assert request(dashboard,'/api/general-schedules','POST',body)[0]==400
