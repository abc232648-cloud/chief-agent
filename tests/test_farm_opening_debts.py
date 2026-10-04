import pytest
from domains.farming import bookkeeping as books, finance
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_bookkeeping import entry


@pytest.mark.parametrize('kind,total,cash', [('OPENING_RECEIVABLE','receivable','received'),('OPENING_PAYABLE','payable','paid')])
def test_opening_partial_full_and_invoice_never_double_count(dashboard,kind,total,cash):
    d=dashboard; p=d.credentials['principal']; debt=entry(kind=kind)
    books.append(d.store,p,debt)
    assert books.append(d.store,p,debt)['status']=='ALREADY_RECORDED'
    for index,amount in enumerate([3000,7000]):
        payment=entry(kind='PAYMENT_CLAIM',amount_minor=amount,reference=debt['event_id'],receipt_ref=f'debt-receipt-{index}')
        books.append(d.store,p,payment)
        books.append(d.store,p,entry(kind='CONFIRM_PAYMENT',amount_minor=0,reference=payment['event_id']))
        if index==0:
            report=finance.report(d.store,p)
            assert report['summaries']['NGN'][total]=='7000'
            assert report['balances'][0]['due_on'] is None and not report['balances'][0]['overdue']
    document=entry(kind='DEBT_DOCUMENT',reference=debt['event_id'],receipt_ref='later-invoice-1')
    books.append(d.store,p,document)
    assert books.append(d.store,p,document)['status']=='ALREADY_RECORDED'
    report=finance.report(d.store,p); totals=report['summaries']['NGN']
    assert totals[total]=='0' and totals[cash]=='10000'
    assert totals['sales']==totals['expenses']=='0'
    assert len(report['balances'])==1
    assert len(books.overview(d.store,p)['records'])==6
    with pytest.raises(ValueError):
        books.append(d.store,p,entry(kind='PAYMENT_CLAIM',reference=debt['event_id'],amount_minor=1,receipt_ref='excess'))
    with pytest.raises(ValueError): books.append(d.store,p,entry(kind='DEBT_DOCUMENT',reference=debt['event_id'],receipt_ref='another-invoice'))


def test_debts_do_not_net_and_invoice_validation_preserves_history(dashboard):
    d=dashboard;p=d.credentials['principal'];a=entry(kind='OPENING_RECEIVABLE');b=entry(kind='OPENING_PAYABLE')
    books.append(d.store,p,a);books.append(d.store,p,b)
    totals=finance.report(d.store,p)['summaries']['NGN']
    assert totals['receivable']==totals['payable']=='10000'
    for changes in [{'amount_minor':5000},{'currency':'USD'},{'counterparty':'Other supplier'},{'reference':None}]:
        with pytest.raises(ValueError): books.append(d.store,p,entry(**{'kind':'DEBT_DOCUMENT','reference':a['event_id'],'receipt_ref':'later-invoice',**changes}))
    doc=entry(kind='DEBT_DOCUMENT',reference=a['event_id'],receipt_ref='later-invoice')
    books.append(d.store,p,doc)
    with pytest.raises(ValueError): books.append(d.store,p,entry(kind='VOID',amount_minor=0,reference=a['event_id']))
    books.append(d.store,p,entry(kind='VOID',amount_minor=0,reference=doc['event_id']))
    books.append(d.store,p,entry(kind='VOID',amount_minor=0,reference=a['event_id']))
    assert len(books.overview(d.store,p)['records'])==5
    assert finance.report(d.store,p)['summaries']['NGN']['receivable']=='0'


def test_manager_reports_debt_but_cannot_confirm_or_reconcile(dashboard):
    d=dashboard;owner=d.credentials['principal'];service=IdentityService(d.store)
    service.create_user(owner,'opening-manager',PASSWORD,'Manager',('farming',));_,manager=service.login('opening-manager',PASSWORD)
    debt=entry(kind='OPENING_PAYABLE');books.append(d.store,manager,debt)
    payment=entry(kind='PAYMENT_CLAIM',reference=debt['event_id'],receipt_ref='reported-payment')
    books.append(d.store,manager,payment)
    with pytest.raises(PermissionError): books.append(d.store,manager,entry(kind='CONFIRM_PAYMENT',reference=payment['event_id'],amount_minor=0))
    with pytest.raises(PermissionError): books.append(d.store,manager,entry(kind='DEBT_DOCUMENT',reference=debt['event_id'],receipt_ref='invoice'))


def test_dispute_blocks_confirmation_until_owner_resolution_and_preserves_history(dashboard):
    d=dashboard; owner=d.credentials['principal']; service=IdentityService(d.store)
    service.create_user(owner,'dispute-manager',PASSWORD,'Manager',('farming',))
    _,manager=service.login('dispute-manager',PASSWORD)
    debt=entry(kind='OPENING_PAYABLE'); books.append(d.store,manager,debt)
    dispute=entry(kind='DISPUTE_DEBT',amount_minor=0,reference=debt['event_id'])
    books.append(d.store,manager,dispute)
    assert books.append(d.store,manager,dispute)['status']=='ALREADY_RECORDED'
    with pytest.raises(ValueError,match='unresolved dispute'):
        books.append(d.store,owner,entry(kind='DISPUTE_DEBT',amount_minor=0,reference=debt['event_id']))
    payment=entry(kind='PAYMENT_CLAIM',reference=debt['event_id'],receipt_ref='disputed-payment')
    books.append(d.store,manager,payment)
    confirmation=entry(kind='CONFIRM_PAYMENT',amount_minor=0,reference=payment['event_id'])
    with pytest.raises(ValueError,match='Resolve the debt dispute'):
        books.append(d.store,owner,confirmation)
    assert finance.report(d.store,owner)['balances'][0]['disputed'] is True
    resolution=entry(kind='RESOLVE_DEBT_DISPUTE',amount_minor=0,reference=dispute['event_id'])
    with pytest.raises(PermissionError): books.append(d.store,manager,resolution)
    books.append(d.store,owner,resolution)
    assert books.append(d.store,owner,resolution)['status']=='ALREADY_RECORDED'
    with pytest.raises(ValueError,match='already resolved'):
        books.append(d.store,owner,entry(kind='RESOLVE_DEBT_DISPUTE',amount_minor=0,reference=dispute['event_id']))
    books.append(d.store,owner,confirmation)
    assert finance.report(d.store,owner)['balances'][0]['disputed'] is False
    books.append(d.store,owner,entry(kind='VOID',amount_minor=0,reference=resolution['event_id']))
    report=finance.report(d.store,owner)
    assert report['balances'][0]['disputed'] is True
    assert report['summaries']['NGN']['paid']=='10000'
    assert report['summaries']['NGN']['payable']=='0'
    assert len(books.overview(d.store,owner)['records'])==6


def test_dispute_rejects_amount_and_non_obligation_reference(dashboard):
    d=dashboard; owner=d.credentials['principal']
    debt=entry(kind='OPENING_RECEIVABLE'); books.append(d.store,owner,debt)
    for changes in ({'amount_minor':1},{'reference':None}):
        with pytest.raises(ValueError):
            books.append(d.store,owner,entry(**{'kind':'DISPUTE_DEBT','amount_minor':0,'reference':debt['event_id'],**changes}))


def test_browser_opening_debt_is_not_sale(dashboard):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url+'/work')
            page.get_by_role('button',name='Expand all sections',exact=True).click()
            page.select_option('#farmMoneyKind','OPENING_RECEIVABLE')
            page.fill('#farmMoneyAmount','100.00');page.fill('#farmCounterparty','Historical customer')
            page.fill('#farmMoneyObserved','2025-01-01T08:00');page.fill('#farmMoneyReason','Opening debt confirmed from prior records')
            page.get_by_role('button',name='Save bookkeeping record',exact=True).click()
            expect(page.locator('#farmMoneyResult')).to_have_text('Saved.')
            expect(page.locator('#financeLedger')).to_contain_text('Opening debt owed to the farm')
            assert finance.report(dashboard.store,dashboard.credentials['principal'])['summaries']['NGN']['sales']=='0'
        finally:browser.close()
