import uuid
import hashlib
import json
import time
import pytest
from domains.farming import recipients, finance
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_identity_http import request


def settings(ids=(), revision=None):
    return {'event_id':str(uuid.uuid4()),'expected_revision':revision,'manager_ids':list(ids)}


def event(category='OPERATIONAL', **changes):
    return {'id':str(uuid.uuid4()),'category':category,'observed_at':utc_text(utc_now()),'historical':False,**changes}


def test_owner_settings_scope_stale_revision_and_revoked_recipient(dashboard):
    d=dashboard;owner=d.credentials['principal'];service=IdentityService(d.store)
    mid=service.create_user(owner,'recipient-manager',PASSWORD,'Manager',('farming',))
    raw,manager=service.login('recipient-manager',PASSWORD)
    p=settings([mid]);recipients.configure(d.store,owner,p)
    assert recipients.configure(d.store,owner,p)['status']=='ALREADY_RECORDED'
    with pytest.raises(ValueError):recipients.configure(d.store,owner,settings())
    assert request(d,'/api/farm/notification-recipients',raw=raw)[0]==403
    with pytest.raises(PermissionError):recipients.configure(d.store,manager,settings())
    assert {r['recipient'] for r in recipients.plan(d.store,owner,event())}=={owner.id,mid}
    assert {r['recipient'] for r in recipients.plan(d.store,owner,event('FINANCIAL'))}=={owner.id}
    service.disable_user(owner,mid)
    assert {r['recipient'] for r in recipients.plan(d.store,owner,event())}=={owner.id}
    with pytest.raises(ValueError):recipients.configure(d.store,owner,settings([mid],p['event_id']))


def test_unscoped_and_worker_recipients_rejected(dashboard):
    d=dashboard;owner=d.credentials['principal'];service=IdentityService(d.store)
    for username,role,scope in [('wrong-scope','Manager','jobs'),('recipient-worker','Worker','farming')]:
        uid=service.create_user(owner,username,PASSWORD,role,(scope,))
        with pytest.raises(ValueError):recipients.configure(d.store,owner,settings([uid]))


def test_synthetic_delivery_retry_and_historical_suppression(dashboard,monkeypatch):
    d=dashboard;owner=d.credentials['principal'];recipients.configure(d.store,owner,settings())
    assert recipients.plan(d.store,owner,event(historical=True))==[]
    assert recipients.plan(d.store,owner,event(observed_at='2025-01-01T00:00:00Z'))==[]
    e=event();assert recipients.synthetic_delivery(d.store,owner,e,fail=True)[0]['status']=='FAILED'
    assert recipients.synthetic_delivery(d.store,owner,e)[0]['status']=='SENT'
    with d.store._connect() as con:before=recipients.rows(con,recipients.DELIVERY)
    assert recipients.synthetic_delivery(d.store,owner,e)[0]['status']=='SENT'
    with d.store._connect() as con:assert recipients.rows(con,recipients.DELIVERY)==before
    assert all('body' not in r and 'email' not in r and 'phone' not in r for r in before)
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','preview')
    with pytest.raises(PermissionError):recipients.synthetic_delivery(d.store,owner,event())


def test_explicit_contact_fields_do_not_reinterpret_legacy_contacts(dashboard):
    d=dashboard;owner=d.credentials['principal']
    old={'operation':'contact','event_id':str(uuid.uuid4()),'name':'Legacy contact','type':'BOTH','contact':'Original note'}
    finance.append(d.store,owner,old)
    new={**old,'event_id':str(uuid.uuid4()),'name':'Structured contact','phone':'+234 123 456 7890','email':'synthetic@example.invalid'}
    finance.append(d.store,owner,new)
    records=finance.report(d.store,owner)['contacts']
    assert 'phone' not in records[0] and records[0]['contact']=='Original note'
    assert records[1]['email']=='synthetic@example.invalid'
    with pytest.raises(ValueError):finance.append(d.store,owner,{**new,'event_id':str(uuid.uuid4()),'email':'x@example.invalid\nBcc: other@example.invalid'})


def test_ambiguous_pending_is_not_replayed(dashboard):
    d=dashboard;owner=d.credentials['principal'];recipients.configure(d.store,owner,settings())
    e=event();target=recipients.plan(d.store,owner,e)[0]
    key=hashlib.sha256(json.dumps(target,sort_keys=True).encode()).hexdigest()
    pending={'id':key,**target,'status':'PENDING','adapter':'SYNTHETIC','received_at':utc_text(utc_now())}
    with d.store._connect() as con:
        con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',('farming',recipients.DELIVERY,json.dumps(pending),time.time()))
    assert recipients.synthetic_delivery(d.store,owner,e)==[{'id':key,'status':'PENDING'}]
    with d.store._connect() as con:assert recipients.rows(con,recipients.DELIVERY)==[pending]


def test_revocation_between_plan_and_dispatch(dashboard,monkeypatch):
    d=dashboard;owner=d.credentials['principal'];service=IdentityService(d.store)
    mid=service.create_user(owner,'revoked-recipient',PASSWORD,'Manager',('farming',))
    recipients.configure(d.store,owner,settings([mid]))
    original=recipients.plan;first=True
    def revoke_after_plan(*args):
        nonlocal first
        result=original(*args)
        if first:
            first=False;service.disable_user(owner,mid)
        return result
    monkeypatch.setattr(recipients,'plan',revoke_after_plan)
    recipients.synthetic_delivery(d.store,owner,event())
    with d.store._connect() as con:
        assert {r['recipient'] for r in recipients.rows(con,recipients.DELIVERY)}=={owner.id}


def test_owner_browser_recipient_settings(dashboard):
    from playwright.sync_api import sync_playwright,expect
    d=dashboard;owner=d.credentials['principal']
    IdentityService(d.store).create_user(owner,'browser-recipient',PASSWORD,'Manager',('farming',))
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work')
            page.get_by_role('button',name='Expand all sections',exact=True).click()
            page.get_by_label('browser-recipient — operational notices').check()
            page.get_by_role('button',name='Save notification recipients',exact=True).click()
            expect(page.locator('#farmRecipientResult')).to_have_text('Recipient preferences saved. External delivery remains unconfigured.')
        finally:browser.close()
