import uuid
import pytest
from domains.farming import bookkeeping as books
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_setup import assign
from tests.test_identity_http import request


def entry(**changes):
    return {'event_id':str(uuid.uuid4()),'kind':'SALE','amount_minor':10000,'currency':'NGN',
            'reference':None,'counterparty':'Synthetic buyer','receipt_ref':'','observed_at':'2026-01-01T00:00:00Z',
            'reason':'Synthetic record',**changes}


def test_sale_partial_payment_and_confirmation_remain_separate(dashboard):
    d=dashboard;p=d.credentials['principal'];sale=entry()
    books.append(d.store,p,sale)
    payment=entry(kind='PAYMENT_CLAIM',reference=sale['event_id'],amount_minor=3000,receipt_ref='receipt-1')
    books.append(d.store,p,payment)
    b=books.overview(d.store,p)['balances'][0]
    assert b['outstanding_minor']==10000 and b['unconfirmed_payment_minor']==3000
    books.append(d.store,p,entry(kind='CONFIRM_PAYMENT',reference=payment['event_id'],amount_minor=0))
    b=books.overview(d.store,p)['balances'][0]
    assert b['outstanding_minor']==7000 and b['confirmed_paid_minor']==3000
    with pytest.raises(ValueError):books.append(d.store,p,entry(kind='PAYMENT_CLAIM',reference=sale['event_id'],amount_minor=8000,receipt_ref='receipt-2'))
    with pytest.raises(ValueError):books.append(d.store,p,entry(kind='PAYMENT_CLAIM',reference=sale['event_id'],amount_minor=1000,receipt_ref='receipt-1'))
    with pytest.raises(ValueError):books.append(d.store,p,entry(kind='PAYMENT_CLAIM',reference=sale['event_id'],amount_minor=1000,receipt_ref='receipt-3',currency='USD'))


def test_rejected_request_does_not_erase_reported_expense(dashboard):
    d=dashboard;p=d.credentials['principal'];req=entry(kind='PURCHASE_REQUEST')
    books.append(d.store,p,req)
    books.append(d.store,p,entry(kind='REJECT_REQUEST',reference=req['event_id'],amount_minor=0))
    books.append(d.store,p,entry(kind='EXPENSE_CLAIM',reference=req['event_id']))
    assert len(books.overview(d.store,p)['records'])==3
    with pytest.raises(ValueError):books.append(d.store,p,entry(kind='APPROVE_REQUEST',reference=req['event_id'],amount_minor=0))


def test_no_retroactive_approval_and_history_preserving_void(dashboard):
    d=dashboard;p=d.credentials['principal'];req=entry(kind='PURCHASE_REQUEST');expense=entry(kind='EXPENSE_CLAIM',reference=req['event_id'])
    books.append(d.store,p,req);books.append(d.store,p,expense)
    with pytest.raises(ValueError):books.append(d.store,p,entry(kind='APPROVE_REQUEST',reference=req['event_id'],amount_minor=0))
    books.append(d.store,p,entry(kind='VOID',reference=expense['event_id'],amount_minor=0))
    assert len(books.overview(d.store,p)['records'])==3
    assert books.overview(d.store,p)['balances']==[]


def test_finance_privacy_and_owner_only_confirmation(dashboard):
    d=dashboard;owner=d.credentials['principal'];service=IdentityService(d.store)
    uid=service.create_user(owner,'farm-finance-manager',PASSWORD,'Manager',('farming',))
    _,manager=service.login('farm-finance-manager',PASSWORD)
    sale=entry();books.append(d.store,manager,sale)
    with pytest.raises(PermissionError):books.append(d.store,manager,entry(kind='VOID',reference=sale['event_id'],amount_minor=0))
    assign(d.store,owner,uid,'SUPERVISOR')
    with pytest.raises(PermissionError):books.overview(d.store,manager)
    with pytest.raises(PermissionError):books.append(d.store,manager,entry())
    service.create_user(owner,'job-finance-manager',PASSWORD,'Manager',('jobs',))
    raw,_=service.login('job-finance-manager',PASSWORD)
    assert request(d,'/api/farm/bookkeeping',raw=raw)[0]==403


@pytest.mark.parametrize('changes',[{'amount_minor':True},{'amount_minor':1.5},{'currency':'UNKNOWN'},{'observed_at':'2026-01-01'},{'actor_id':'forged'}])
def test_invalid_finance_fields(dashboard,changes):
    with pytest.raises(ValueError):books.append(dashboard.store,dashboard.credentials['principal'],entry(**changes))


def test_browser_records_currency_amount_without_float_rounding(dashboard):
    from playwright.sync_api import sync_playwright,expect
    d=dashboard
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work')
            page.get_by_role('button', name='Expand all sections', exact=True).click()
            expect(page.locator('#farmFinanceCard')).to_be_visible()
            page.select_option('#farmMoneyKind','SALE');page.fill('#farmMoneyAmount','2500.50')
            page.fill('#farmCounterparty','Synthetic buyer');page.fill('#farmMoneyObserved','2026-01-01T08:00')
            page.fill('#farmMoneyReason','Synthetic sale')
            page.get_by_role('button',name='Save bookkeeping record',exact=True).click()
            expect(page.locator('#farmMoneyResult')).to_have_text('Saved.')
            expect(page.locator('#farmMoneyBalances')).to_contain_text('NGN 2500.50')
            assert books.overview(d.store,d.credentials['principal'])['balances'][0]['amount_minor']==250050
        finally:browser.close()
