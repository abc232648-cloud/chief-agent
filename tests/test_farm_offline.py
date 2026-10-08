import base64
import json
import uuid
from playwright.sync_api import sync_playwright,expect
from domains.farming import setup,journal,staff,photos
from tests.test_farm_setup import entity
from tests.test_farm_journal import record
from tests.test_farm_staff import event
from tests.test_farm_photos import png
from tests.checkpoint_f_fixture import PASSWORD
from identity.service import IdentityService

PHRASE='Synthetic offline fixture passphrase only'


def configured(d):
    e=entity();setup.append(d.store,d.credentials['principal'],e)
    return e,record(entity_id=e['entity_id'],location=e['name'],notes='PRIVATE_OFFLINE_MARKER')


def work_photo(d):
    work=event(text='PRIVATE_MEDIA_WORK_MARKER');staff.append(d.store,d.credentials['principal'],work)
    return work,{'event_id':str(uuid.uuid4()),'work_id':work['event_id'],'image_base64':base64.b64encode(png()).decode()}


def test_offline_restart_encrypted_queue_and_explicit_sync(dashboard):
    d=dashboard;e,payload=configured(d)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work')
            page.evaluate("async()=>{const c=await caches.open('farm-public-v1');await c.put('/static/dashboard.css',new Response('obsolete-style'));}")
            page.get_by_role('button',name='Offline reports',exact=True).click()
            expect(page).to_have_url(d.url+'/work?offline=1')
            expect(page.locator('h1')).to_have_text('Private pending reports')
            info=page.evaluate('(phrase)=>FarmOutbox.provision(phrase)',PHRASE)
            page.evaluate('(payload)=>FarmOutbox.queue(payload)',payload)
            raw=page.evaluate("async()=>{const db=await new Promise(r=>{const q=indexedDB.open('farm-private-outbox-v1',2);q.onsuccess=()=>r(q.result)});return new Promise(r=>{const q=db.transaction('profiles').objectStore('profiles').getAll();q.onsuccess=()=>{db.close();r(JSON.stringify(q.result))}})}")
            assert 'PRIVATE_OFFLINE_MARKER' not in raw and e['name'] not in raw and PHRASE not in raw
            page.context.set_offline(True);page.goto(d.url+'/work')
            expect(page.locator('h1')).to_have_text('Private pending reports')
            page.evaluate('args=>FarmOutbox.unlock(args.id,args.phrase)',{'id':info['id'],'phrase':PHRASE})
            assert page.evaluate('async()=> (await FarmOutbox.view()).pending.length')==1
            assert journal.overview(d.store,d.credentials['principal'])['record_count']==0
            page.context.set_offline(False)
            result=page.evaluate('()=>FarmOutbox.sync()')
            assert result=={'sent':1,'pending':0}
            assert journal.overview(d.store,d.credentials['principal'])['record_count']==1
            paths=page.evaluate("async()=>{const c=await caches.open('farm-public-v3');return (await c.keys()).map(r=>new URL(r.url).pathname)}")
            assert paths and all(path.startswith('/static/') for path in paths)
            assert page.evaluate("async()=>!(await caches.keys()).includes('farm-public-v1')")
            assert not any('/api/' in path or path=='/work' for path in paths)
        finally:browser.close()


def test_wrong_account_conflicts_and_lost_response_never_duplicate(dashboard):
    d=dashboard;_,payload=configured(d);service=IdentityService(d.store)
    service.create_user(d.credentials['principal'],'other-offline',PASSWORD,'Worker',('farming',))
    other,_=service.login('other-offline',PASSWORD)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/static/farm-offline.html')
            page.evaluate('(phrase)=>FarmOutbox.provision(phrase)',PHRASE)
            page.evaluate('(payload)=>FarmOutbox.queue(payload)',payload)
            page.context.add_cookies([{'name':'chief_session','value':other,'url':d.url,'httpOnly':True,'sameSite':'Strict'}])
            error=page.evaluate("async()=>{try{await FarmOutbox.sync();return '';}catch(e){return e.message;}}")
            assert 'different person' in error
            assert journal.overview(d.store,d.credentials['principal'])['record_count']==0
            page.context.add_cookies([{'name':'chief_session','value':d.credentials['raw'],'url':d.url,'httpOnly':True,'sameSite':'Strict'}])
            def lose_response(route):
                route.fetch();route.abort('failed')
            page.route('**/api/farm/journal',lose_response)
            error=page.evaluate("async()=>{try{await FarmOutbox.sync();return '';}catch(e){return e.message;}}")
            assert 'interrupted' in error
            assert journal.overview(d.store,d.credentials['principal'])['record_count']==1
            page.unroute('**/api/farm/journal',lose_response)
            assert page.evaluate('()=>FarmOutbox.sync()')=={'sent':1,'pending':0}
            assert journal.overview(d.store,d.credentials['principal'])['record_count']==1
            invalid={**payload,'event_id':str(uuid.uuid4()),'quantity':'-3'}
            page.evaluate('(payload)=>FarmOutbox.queue(payload)',invalid)
            assert page.evaluate('()=>FarmOutbox.sync()')=={'sent':0,'pending':1}
            assert page.evaluate('async()=> (await FarmOutbox.view()).pending[0].status')=='CONFLICT'
            page.evaluate('()=>FarmOutbox.lock()')
            assert page.evaluate("async()=>{try{await FarmOutbox.view();return false;}catch{return true;}}") is True
        finally:browser.close()


def test_offline_media_is_encrypted_and_only_clears_after_authoritative_listing(dashboard):
    d=dashboard;work,payload=work_photo(d)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/static/farm-offline.html')
            page.evaluate('(phrase)=>FarmOutbox.provision(phrase)',PHRASE)
            page.evaluate('(payload)=>FarmOutbox.queueMedia(payload)',payload)
            assert page.evaluate('async()=> (await FarmOutbox.mediaView()).length')==1
            raw=page.evaluate("async()=>{const db=await new Promise(r=>{const q=indexedDB.open('farm-private-outbox-v1',2);q.onsuccess=()=>r(q.result)});return new Promise(r=>{const q=db.transaction('media').objectStore('media').getAll();q.onsuccess=()=>{db.close();r(JSON.stringify(q.result))}})}")
            assert payload['image_base64'] not in raw and work['event_id'] not in raw and 'PRIVATE_MEDIA_WORK_MARKER' not in raw
            result=page.evaluate('()=>FarmOutbox.syncMedia()')
            assert result=={'reconciled':1,'pending':0}
            listing=photos.listing(d.store,d.credentials['principal'],work['event_id'])
            assert len(listing)==1 and listing[0]['id']==payload['event_id'] and listing[0]['sha256']
        finally:browser.close()


def test_media_upload_receipt_is_retained_until_independent_reconciliation(dashboard):
    d=dashboard;work,payload=work_photo(d)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/static/farm-offline.html')
            page.evaluate('(phrase)=>FarmOutbox.provision(phrase)',PHRASE)
            page.evaluate('(payload)=>FarmOutbox.queueMedia(payload)',payload)
            def hide_listing(route):
                if route.request.method=='GET':route.fulfill(status=200,content_type='application/json',body='[]')
                else:route.continue_()
            page.route('**/api/farm/photos?work_id=*',hide_listing)
            first=page.evaluate('()=>FarmOutbox.syncMedia()')
            assert first=={'reconciled':0,'pending':1}
            queued=page.evaluate('async()=> (await FarmOutbox.mediaView())[0]')
            assert queued['status']=='RECONCILE' and queued['receipt']['id']==payload['event_id']
            assert len(photos.listing(d.store,d.credentials['principal'],work['event_id']))==1
            page.unroute('**/api/farm/photos?work_id=*',hide_listing)
            second=page.evaluate('()=>FarmOutbox.syncMedia()')
            assert second=={'reconciled':1,'pending':0}
            assert len(photos.listing(d.store,d.credentials['principal'],work['event_id']))==1
        finally:browser.close()


def test_media_authoritative_metadata_mismatch_fails_closed(dashboard):
    d=dashboard;_,payload=work_photo(d)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/static/farm-offline.html')
            page.evaluate('(phrase)=>FarmOutbox.provision(phrase)',PHRASE)
            page.evaluate('(payload)=>FarmOutbox.queueMedia(payload)',payload)
            calls={'listing':0}
            def mismatch(route):
                if route.request.method=='GET':
                    calls['listing']+=1
                    actual=photos.listing(d.store,d.credentials['principal'],payload['work_id'])
                    if actual:
                        forged=[{**actual[0],'sha256':'0'*64}]
                        route.fulfill(status=200,content_type='application/json',body=json.dumps(forged));return
                route.continue_()
            page.route('**/api/farm/photos?work_id=*',mismatch)
            result=page.evaluate('()=>FarmOutbox.syncMedia()')
            assert result=={'reconciled':0,'pending':1}
            queued=page.evaluate('async()=> (await FarmOutbox.mediaView())[0]')
            assert queued['status']=='CONFLICT' and 'conflicts' in queued['reason']
            assert calls['listing']>=1
        finally:browser.close()
