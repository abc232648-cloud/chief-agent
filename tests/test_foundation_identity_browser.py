from datetime import timedelta
import re
from playwright.sync_api import sync_playwright,expect
from operations.time_integrity import utc_now,utc_text
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_identity_http import request


def stamp(d):
    with d.store._connect() as con:return con.execute('SELECT last_seen FROM human_sessions WHERE id=?',(d.credentials['principal'].session_id,)).fetchone()[0]


def age(d,minutes):
    value=utc_text(utc_now()-timedelta(minutes=minutes))
    with d.store._connect() as con:con.execute('UPDATE human_sessions SET last_seen=? WHERE id=?',(value,d.credentials['principal'].session_id))
    return value


def test_visible_idle_tab_and_background_polls_do_not_renew(dashboard):
    d=dashboard
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page();page.goto(d.url)
        expect(page.locator('#navLinks')).to_be_visible()
        old=age(d,9)
        page.wait_for_timeout(5500) # Observe an actual five-second dashboard poll.
        assert stamp(d)==old
        age(d,11)
        page.wait_for_url(re.compile(r'.*/login\?expired=1'),timeout=12000)
        expect(page.locator('#result')).to_contain_text('Unsaved changes were not submitted')
        assert page.evaluate('localStorage.length')==0
        browser.close()


def test_only_trusted_interaction_sends_activity(dashboard):
    d=dashboard
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page();page.goto(d.url)
        expect(page.locator('#navLinks')).to_be_visible();old=age(d,9)
        page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'a',bubbles:true}))")
        page.wait_for_timeout(200);assert stamp(d)==old
        with page.expect_response(lambda r:r.url.endswith('/api/auth/activity')) as response:
            page.locator('#overview h1').click()
        assert response.value.status==200
        assert stamp(d)!=old
        browser.close()


def test_rotation_and_logout_apply_across_tabs_without_action_replay(dashboard):
    d=dashboard
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);context=browser.new_context()
        first=context.new_page();second=context.new_page()
        for page in (first,second):page.goto(d.url);expect(page.locator('#navLinks')).to_be_visible()
        old=d.credentials['raw']
        code=first.evaluate("async password=>(await fetch('/api/auth/reauthenticate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password})})).status",PASSWORD)
        assert code==200 and request(d,'/api/auth/session',raw=old)[0]==401
        assert second.evaluate("async()=>(await fetch('/api/auth/activity',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).status")==200
        second.get_by_role('button',name='Sign out',exact=True).click()
        first.wait_for_url(re.compile(r'.*/login.*'),timeout=12000)
        assert first.evaluate("async()=>(await fetch('/api/state')).status")==401
        assert d.store.counts()['commands']==0
        browser.close()
