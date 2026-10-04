import json
import pytest
from domains.farming import finance, bookkeeping as books
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_bookkeeping import entry
from tests.test_identity_http import request


def chain(d):
    owner=d.credentials['principal'];opening=entry(kind='OPENING_RECEIVABLE')
    payment=entry(kind='PAYMENT_CLAIM',amount_minor=3000,reference=opening['event_id'],receipt_ref='trace-receipt')
    confirmation=entry(kind='CONFIRM_PAYMENT',amount_minor=0,reference=payment['event_id'])
    invoice=entry(kind='DEBT_DOCUMENT',reference=opening['event_id'],receipt_ref='trace-invoice')
    void=entry(kind='VOID',amount_minor=0,reference=confirmation['event_id'])
    for row in [opening,payment,confirmation,invoice,void]:books.append(d.store,owner,row)
    return [opening,payment,confirmation,invoice,void]


def test_trace_preserves_voided_original_and_approval_actor(dashboard):
    d=dashboard;rows=chain(d)
    trace=finance.linked_history(d.store,d.credentials['principal'],rows[1]['event_id'])
    assert {r['payload']['event_id'] for r in trace['records']}=={p['event_id'] for p in rows}
    assert not trace['truncated']
    confirmation=next(r for r in trace['records'] if r['payload']['kind']=='CONFIRM_PAYMENT')
    assert not confirmation['is_current'] and confirmation['actor']
    assert all(r['received_at'] and r['payload']['observed_at'] for r in trace['records'])
    assert 'session_id' not in json.dumps(trace) and PASSWORD not in json.dumps(trace)
    assert books.overview(d.store,d.credentials['principal'])['balances'][0]['outstanding_minor']==10000


def test_trace_endpoint_role_and_domain_boundaries(dashboard):
    d=dashboard;rows=chain(d);owner=d.credentials['principal'];service=IdentityService(d.store)
    path='/api/farm/finance/history/'+rows[0]['event_id']
    assert request(d,path,raw=d.credentials['raw'])[0]==200
    for name,role,domain in [('trace-worker','Worker','farming'),('trace-job','Manager','jobs')]:
        service.create_user(owner,name,PASSWORD,role,(domain,));raw,_=service.login(name,PASSWORD)
        assert request(d,path,raw=raw)[0]==403
    assert request(d,path)[0]==401
    with pytest.raises(FileNotFoundError):finance.linked_history(d.store,owner,'missing-record-id')


def test_browser_readable_linked_history(dashboard):
    from playwright.sync_api import sync_playwright,expect
    d=dashboard;chain(d)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work')
            page.get_by_role('button',name='Expand all sections',exact=True).click()
            page.get_by_role('button',name='View linked history',exact=True).first.click()
            panel=page.locator('.financeLinkedHistory')
            expect(panel).to_contain_text('Voided original')
            expect(panel).to_contain_text('Payment confirmed')
            expect(panel).to_contain_text('Recorded by')
            expect(panel).to_contain_text('Invoice attached to opening debt')
        finally:browser.close()
