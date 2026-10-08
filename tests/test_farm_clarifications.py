import copy
import json
import uuid
import pytest
from domains.farming import clarifications as c, journal, bookkeeping as books, planning, setup, reconciliation
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_journal import record
from tests.test_farm_bookkeeping import entry
from tests.test_farm_planning import scenario, save_payload
from tests.test_farm_setup import entity, assign
from tests.test_identity_http import request


def create(d, template, field=None):
    owner = d.credentials['principal']
    source = next(s for s in c.overview(d.store, owner)['sources'] if s['template'] == template and (field is None or s['source']['field'] == field))
    command = dict(event_id=str(uuid.uuid4()), operation='create', template=template, source=source['source'])
    c.append(d.store, owner, command)
    return command


def respond(d, item, option='unknown', data=None, notes='', principal=None, defer_until=None):
    owner = d.credentials['principal']
    current = next(i for i in c.overview(d.store, owner)['items'] if i['id'] == item['event_id'])
    p = dict(event_id=str(uuid.uuid4()), operation='answer', item_id=current['id'], expected_revision=current['revision'],
             option=option, data=data or {}, notes=notes, defer_until=defer_until)
    return c.append(d.store, principal or owner, p), p


def accept(d, item, principal=None):
    owner = d.credentials['principal']; current = next(i for i in c.overview(d.store, owner)['items'] if i['id'] == item['event_id'])
    p = dict(event_id=str(uuid.uuid4()), operation='apply', item_id=current['id'], expected_revision=current['revision'])
    return c.append(d.store, principal or owner, p), p


def feed(d):
    p = record(kind='feed_used', quantity='2')
    journal.append(d.store, d.credentials['principal'], p)
    return p


def principal(d, role, name='test-person', domains=('farming',)):
    service = IdentityService(d.store)
    uid = service.create_user(d.credentials['principal'], name, PASSWORD, role, domains)
    return uid, service.login(name, PASSWORD)[1]


def test_unknown_notes_deferral_no_schema_or_stock_changes(dashboard):
    d=dashboard; feed(d); owner=d.credentials['principal']
    with d.store._connect() as con:
        schema=list(map(tuple,con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))
        legacy=list(map(tuple,con.execute("SELECT * FROM domain_records WHERE kind!=?",(c.KIND,))))
    item=create(d,'feed'); result,p=respond(d,item,notes='<script>do not execute</script>')
    assert result['record']['state']=='OPEN'
    assert c.append(d.store,owner,p)['status']=='ALREADY_RECORDED'
    view=c.overview(d.store,owner);assert view['unresolved']==1 and view['items'][0]['deferred']
    with pytest.raises(ValueError):accept(d,item)
    with d.store._connect() as con:
        assert schema==list(map(tuple,con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))
        assert legacy==list(map(tuple,con.execute("SELECT * FROM domain_records WHERE kind!=?",(c.KIND,))))


def test_idempotent_create_duplicate_source_and_different_record(dashboard):
    d=dashboard;feed(d);owner=d.credentials['principal'];p=create(d,'feed')
    assert c.append(d.store,owner,p)['status']=='ALREADY_RECORDED'
    assert c.append(d.store,owner,{**p,'event_id':str(uuid.uuid4())})['status']=='EXISTING_ITEM'
    with pytest.raises(ValueError):c.append(d.store,owner,{**p,'template':'stock'})
    feed(d);sources=c.overview(d.store,owner)['sources'];assert len([s for s in sources if s['template']=='feed'])==2
    refreshed=next(s for s in sources if s['template']=='feed' and s['source']['event_id']==p['source']['event_id'])
    duplicate=c.append(d.store,owner,{**p,'event_id':str(uuid.uuid4()),'source':refreshed['source']})
    assert duplicate=={'status':'EXISTING_ITEM','item_id':p['event_id']}
    assert c.overview(d.store,owner)['unresolved']==1


def test_feed_correction_atomic_idempotent_and_history(dashboard):
    d=dashboard;old=feed(d);p=create(d,'feed')
    respond(d,p,'correct',{'quantity':'3','unit':'kg','observed_at':old['observed_at']})
    result,command=accept(d,p)
    assert result['record']['state']=='ANSWERED' and len(result['record']['effects'])==1
    assert c.append(d.store,d.credentials['principal'],command)['status']=='ALREADY_RECORDED'
    with d.store._connect() as con:
        rows=journal.read_rows(con);assert len(rows)==2 and rows[0]['payload']==journal.validate(old)
        assert journal.effective(rows)[0]['payload']['quantity']=='3'


def test_transaction_failure_rolls_back_domain_action_and_answer(dashboard,monkeypatch):
    d=dashboard;old=feed(d);p=create(d,'feed')
    respond(d,p,'correct',{'quantity':'3','unit':'kg','observed_at':old['observed_at']})
    original=IdentityService._event
    def fail(self,con,actor,operation,*args,**kwargs):
        if operation=='FARM_CLARIFICATION_APPLY':raise RuntimeError('Synthetic interruption')
        return original(self,con,actor,operation,*args,**kwargs)
    monkeypatch.setattr(IdentityService,'_event',fail)
    with pytest.raises(RuntimeError):accept(d,p)
    with d.store._connect() as con:assert len(journal.read_rows(con))==1
    assert c.overview(d.store,d.credentials['principal'])['items'][0]['state']=='AWAITING_APPROVAL'


def test_stale_source_cannot_overwrite_and_can_be_superseded(dashboard):
    d=dashboard;old=feed(d);p=create(d,'feed')
    respond(d,p,'correct',{'quantity':'3','unit':'kg','observed_at':old['observed_at']})
    journal.append(d.store,d.credentials['principal'],{**old,'event_id':str(uuid.uuid4()),'quantity':'4','corrects':old['event_id'],'reason':'Newer correction'})
    with pytest.raises(ValueError,match='Source changed'):accept(d,p)
    item=c.overview(d.store,d.credentials['principal'])['items'][0];assert item['source_changed']
    c.append(d.store,d.credentials['principal'],dict(event_id=str(uuid.uuid4()),operation='supersede',item_id=item['id'],expected_revision=item['revision']))
    assert c.overview(d.store,d.credentials['principal'])['items'][0]['state']=='SUPERSEDED'


def test_manager_and_supervisor_answer_operations_but_cannot_accept(dashboard):
    d=dashboard;old=feed(d);item=create(d,'feed')
    _,manager=principal(d,'Manager','manager')
    respond(d,item,'actual',{'quantity':'1','unit':'kg','observed_at':old['observed_at']},principal=manager)
    with pytest.raises(PermissionError):accept(d,item,manager)
    uid,supervisor=principal(d,'Worker','supervisor');assign(d.store,d.credentials['principal'],uid,'SUPERVISOR')
    respond(d,item,principal=supervisor)
    _,worker=principal(d,'Worker','worker');assert c.overview(d.store,worker)['items']==[]
    with pytest.raises(PermissionError):respond(d,item,principal=worker)


def test_finance_private_and_transfer_owner_confirmation(dashboard):
    d=dashboard;owner=d.credentials['principal'];sale=entry();books.append(d.store,owner,sale)
    payment=entry(kind='PAYMENT_CLAIM',reference=sale['event_id'],amount_minor=3000,receipt_ref='synthetic-receipt')
    books.append(d.store,owner,payment);item=create(d,'transfer');_,manager=principal(d,'Manager')
    assert c.overview(d.store,manager)['items']==[]
    for ref_id in (payment['event_id'], 'missing-private-record'):
        with pytest.raises(PermissionError):
            c.append(d.store,manager,dict(event_id=str(uuid.uuid4()),operation='create',template='transfer',
                source={'kind':books.KIND,'event_id':ref_id,'field':'reference','revision':'0'*64}))
    with pytest.raises(PermissionError):respond(d,item,'confirmed',principal=manager)
    respond(d,item,'reported');accept(d,item)
    assert books.overview(d.store,owner)['balances'][0]['confirmed_paid_minor']==0
    done=c.overview(d.store,owner)['items'][0]
    c.append(d.store,owner,dict(event_id=str(uuid.uuid4()),operation='supersede',item_id=done['id'],expected_revision=done['revision']))
    item=create(d,'transfer');respond(d,item,'confirmed');result,command=accept(d,item)
    assert books.overview(d.store,owner)['balances'][0]['confirmed_paid_minor']==3000
    c.append(d.store,owner,command)
    assert books.overview(d.store,owner)['balances'][0]['confirmed_paid_minor']==3000


def test_price_correction_uses_void_replacement_and_preserves_old(dashboard):
    d=dashboard;owner=d.credentials['principal'];sale=entry();books.append(d.store,owner,sale)
    item=create(d,'price');respond(d,item,'correct',{'amount_minor':9000,'currency':'NGN'})
    accept(d,item)
    with d.store._connect() as con:
        history=books.rows(con);assert history[0]['payload']==books.validate(sale)
        assert [r['payload']['amount_minor'] for r in books.active(history) if r['payload']['kind']=='SALE']==[9000]


def test_price_linked_payment_prevents_unsafe_correction(dashboard):
    d=dashboard;owner=d.credentials['principal'];sale=entry();books.append(d.store,owner,sale)
    books.append(d.store,owner,entry(kind='PAYMENT_CLAIM',reference=sale['event_id'],amount_minor=1000,receipt_ref='receipt-link'))
    item=create(d,'price');respond(d,item,'correct',{'amount_minor':9000,'currency':'NGN'})
    with pytest.raises(ValueError,match='dependent'):accept(d,item)
    assert books.overview(d.store,owner)['balances'][0]['amount_minor']==10000


def test_plan_baseline_creates_version_not_consumption(dashboard):
    d=dashboard;owner=d.credentials['principal'];p=scenario();planning.save(d.store,owner,save_payload(d,p))
    item=create(d,'feed');respond(d,item,'plan',{'value':'8','start':'2026-10-08','end':'2026-10-09'})
    accept(d,item);history=planning.overview(d.store,owner)['records']
    assert len(history)==2 and history[0]['payload']['assumptions']['feed_kg_per_day']=='8'
    assert history[1]['payload']['assumptions']['feed_kg_per_day'] is None
    with d.store._connect() as con:assert journal.read_rows(con)==[]


def test_transport_period_unknown_discount_no_invented_recurrence(dashboard):
    d=dashboard;owner=d.credentials['principal'];books.append(d.store,owner,entry(kind='EXPENSE_CLAIM'))
    item=create(d,'transport')
    with pytest.raises(ValueError):respond(d,item,'other_transport',{'frequency':'recurring','period_start':None,'period_end':None})
    respond(d,item,'other_transport',{'frequency':'unknown','period_start':None,'period_end':None});accept(d,item)
    with d.store._connect() as con:assert len(books.rows(con))==1
    books.append(d.store,owner,entry());item=create(d,'price');respond(d,item)
    assert c.overview(d.store,owner)['items'][0]['state']=='OPEN'


def test_stock_count_does_not_adjust_inventory_and_estimate_stays_annotation(dashboard):
    d=dashboard;owner=d.credentials['principal'];loc=entity();setup.append(d.store,owner,loc)
    original=record(kind='feed_opening',entity_id=loc['entity_id'],location=loc['name'],quantity='10');journal.append(d.store,owner,original)
    item=create(d,'stock');respond(d,item,'counted',{'quantity':'12','unit':'kg','observed_at':original['observed_at']});accept(d,item)
    with d.store._connect() as con:assert len(journal.read_rows(con))==1 and len(reconciliation.rows(con))==1


@pytest.mark.parametrize('change',[{'quantity':'NaN'},{'quantity':True},{'unit':'bags'},{'observed_at':'2035-01-01T00:00:00Z'}])
def test_invalid_typed_followups_rejected(dashboard,change):
    d=dashboard;old=feed(d);item=create(d,'feed')
    with pytest.raises((ValueError,TypeError)):respond(d,item,'actual',{'quantity':'2','unit':'kg','observed_at':old['observed_at'],**change})


def test_cross_domain_revoked_session_api_and_csrf(dashboard):
    d=dashboard;feed(d);p=create(d,'feed');service=IdentityService(d.store)
    _,other=principal(d,'Manager','job-only',('jobs',))
    with pytest.raises(PermissionError):c.overview(d.store,other)
    uid,manager=principal(d,'Manager','revoked');service.disable_user(d.credentials['principal'],uid)
    with pytest.raises(Exception):respond(d,p,principal=manager)
    assert request(d,'/api/farm/clarifications',raw='invalid')[0]==401
    assert request(d,'/api/farm/clarifications',raw=d.credentials['raw'])[0]==200
    assert request(d,'/api/farm/clarifications','POST',p,raw=d.credentials['raw'],csrf=False)[0]==403
    assert request(d,'/api/farm/clarifications','POST',p,raw=d.credentials['raw'],extra={'Origin':'http://untrusted.invalid'})[0]==403


def test_browser_cards_mobile_and_desktop_escape_notes(dashboard,tmp_path):
    from playwright.sync_api import sync_playwright, expect
    import os
    from pathlib import Path
    d=dashboard;feed(d);item=create(d,'feed');respond(d,item,notes='<img src=x onerror="window.CLARIFICATION_XSS=1">')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            for width,name in [(1280,'desktop'),(390,'mobile')]:
                page=browser.new_page(viewport={'width':width,'height':900});page.goto(d.url)
                page.locator('nav .domainNav[data-domain="farming"] > summary').click()
                page.get_by_role('button',name='Poultry records',exact=True).click()
                if width < 600: page.get_by_role('button',name='Toggle sidebar',exact=True).click()
                page.locator('#farmClarifications > summary').click()
                host=page.locator('#farmClarifications');expect(host).to_contain_text('1 unresolved items')
                host.locator('summary').filter(has_text='Feed difference').click()
                host.locator('summary').filter(has_text='Answer history').click()
                expect(host).to_contain_text('<img src=x')
                assert page.evaluate('window.CLARIFICATION_XSS') is None
                assert host.locator('img').count()==0
                evidence=Path(os.environ.get('DASHBOARD_EVIDENCE_DIR',tmp_path));evidence.mkdir(parents=True,exist_ok=True)
                host.scroll_into_view_if_needed()
                page.screenshot(path=str(evidence/f'clarifications-{name}.png'))
                page.close()
        finally:browser.close()


def test_concurrent_duplicate_acceptance_changes_records_once(dashboard):
    from concurrent.futures import ThreadPoolExecutor
    d=dashboard;old=feed(d);item=create(d,'feed');owner=d.credentials['principal']
    respond(d,item,'correct',{'quantity':'5','unit':'kg','observed_at':old['observed_at']})
    current=c.overview(d.store,owner)['items'][0]
    command=dict(event_id=str(uuid.uuid4()),operation='apply',item_id=current['id'],expected_revision=current['revision'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:c.append(d.store,owner,command),range(2)))
    assert sorted(r['status'] for r in results)==['ALREADY_RECORDED','RECORDED']
    with d.store._connect() as con:assert len(journal.read_rows(con))==2


def test_answer_revision_conflict_utc_and_template_snapshot(dashboard):
    d=dashboard;feed(d);item=create(d,'feed');owner=d.credentials['principal']
    _,command=respond(d,item,'actual',{'quantity':'1','unit':'kg','observed_at':'2026-01-01T01:00:00+01:00'})
    current=c.overview(d.store,owner)['items'][0]
    assert current['answer']['data']['observed_at']=='2026-01-01T00:00:00.000000Z'
    assert current['definition']==c.TEMPLATES['feed'] and current['template_version']==c.VERSION
    assert c.append(d.store,owner,command)['status']=='ALREADY_RECORDED'
    with pytest.raises(ValueError,match='changed'):c.append(d.store,owner,{**command,'event_id':str(uuid.uuid4())})


def test_reauthentication_required_for_acceptance(dashboard):
    from datetime import timedelta
    from operations.time_integrity import utc_now,utc_text
    from identity.contracts import ReauthenticationRequired
    d=dashboard;old=feed(d);item=create(d,'feed');owner=d.credentials['principal']
    respond(d,item,'correct',{'quantity':'5','unit':'kg','observed_at':old['observed_at']})
    with d.store._connect() as con:
        con.execute('UPDATE human_sessions SET reauth_at=? WHERE id=?',(utc_text(utc_now()-timedelta(hours=1)),owner.session_id))
    with pytest.raises(ReauthenticationRequired):accept(d,item)
    with d.store._connect() as con:assert len(journal.read_rows(con))==1


def test_accepted_classification_flags_saved_plan_preserves_inputs(dashboard):
    d=dashboard;owner=d.credentials['principal'];books.append(d.store,owner,entry(kind='EXPENSE_CLAIM'))
    p=scenario();planning.save(d.store,owner,save_payload(d,p));before=planning.overview(d.store,owner)['records'][0]
    item=create(d,'transport');respond(d,item,'delivery',{'frequency':'one_off','period_start':None,'period_end':None});accept(d,item)
    after=planning.overview(d.store,owner)['records'][0]
    assert not before['review_needed'] and after['review_needed']
    assert before['payload']==after['payload'] and before['result']==after['result']


def test_actual_classification_does_not_repost_existing_consumption(dashboard):
    d=dashboard;old=feed(d);item=create(d,'number')
    respond(d,item,'actual',{'quantity':old['quantity'],'unit':'kg','observed_at':old['observed_at']})
    accept(d,item)
    with d.store._connect() as con:assert len(journal.read_rows(con))==1
    item=create(d,'feed');respond(d,item,'actual',{'quantity':'3','unit':'kg','observed_at':old['observed_at']})
    with pytest.raises(ValueError,match='Correct an earlier'):accept(d,item)
    with d.store._connect() as con:assert len(journal.read_rows(con))==1


def test_new_feed_observation_records_once_without_changing_old(dashboard):
    from datetime import timedelta
    from operations.time_integrity import aware_utc,utc_text
    d=dashboard;old=feed(d);item=create(d,'feed')
    later=utc_text(aware_utc(old['observed_at'])+timedelta(minutes=1))
    respond(d,item,'actual',{'quantity':'3','unit':'kg','observed_at':later})
    _,command=accept(d,item);c.append(d.store,d.credentials['principal'],command)
    with d.store._connect() as con:
        rows=journal.read_rows(con);assert len(rows)==2
        assert rows[0]['payload']==journal.validate(old) and rows[1]['payload']['quantity']=='3'


def test_browser_manager_answer_form_and_finance_isolation(dashboard):
    from playwright.sync_api import sync_playwright,expect
    d=dashboard;old=feed(d);item=create(d,'feed');owner=d.credentials['principal']
    books.append(d.store,owner,entry(counterparty='Private synthetic customer'));create(d,'price')
    principal(d,'Manager','mobile-manager');raw,_=IdentityService(d.store).login('mobile-manager',PASSWORD)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':390,'height':844})
            page.context.add_cookies([{'name':'chief_session','value':raw,'url':d.url,'httpOnly':True,'sameSite':'Strict'}])
            page.goto(d.url+'/work');host=page.locator('#farmClarifications')
            expect(host).to_contain_text('1 unresolved items');host.locator(':scope > summary').click()
            host.locator('summary').filter(has_text='Feed difference').click()
            expect(host).not_to_contain_text('Private synthetic customer')
            card=host.locator('details.item');card.get_by_label('Answer',exact=True).select_option('unknown')
            card.get_by_label('Optional notes',exact=True).fill('Please revisit when the record is available.')
            card.get_by_role('button',name='Save answer for review',exact=True).click()
            expect(host.locator('details.item > summary')).to_contain_text('deferred')
            assert host.get_by_role('button',name='Accept saved answer',exact=True).count()==0
            assert next(i for i in c.overview(d.store,owner)['items'] if i['id']==item['event_id'])['answer']['notes']=='Please revisit when the record is available.'
        finally:browser.close()
