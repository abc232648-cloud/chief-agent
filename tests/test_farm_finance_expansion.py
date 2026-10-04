import base64,json,sqlite3,uuid
import pytest
from domains.farming import finance,bookkeeping as books
from identity.service import IdentityService
from tests.test_farm_bookkeeping import entry
from tests.test_farm_photos import png
from tests.test_identity_http import request
from tests.checkpoint_f_fixture import PASSWORD,recovery_point

def contact(d,kind='BOTH',name='Synthetic customer'):
    value=dict(operation='contact',event_id=str(uuid.uuid4()),name=name,type=kind,contact='synthetic contact')
    finance.append(d.store,d.credentials['principal'],value)
    return value

def detailed(c,**kwargs):
    return entry(counterparty=c['name'],details={'category':'EGG_SALES','contact_id':c['event_id'],'due_on':'2026-01-10','items':[{'description':'Eggs','amount_minor':10000}]},**kwargs)

def test_exact_financial_summary_partial_payments_dates_and_legacy(dashboard):
    d=dashboard;p=d.credentials['principal'];c=contact(d)
    old=entry();books.append(d.store,p,old)
    with d.store._connect() as con:before=con.execute("SELECT data_json FROM domain_records WHERE kind=?",(books.KIND,)).fetchone()[0]
    sale=detailed(c);books.append(d.store,p,sale)
    pay=entry(kind='PAYMENT_CLAIM',amount_minor=2500,reference=sale['event_id'],receipt_ref='Synthetic receipt')
    books.append(d.store,p,pay)
    assert finance.report(d.store,p,contact_id=c['event_id'])['summaries']['NGN']['received']=='0'
    books.append(d.store,p,entry(kind='CONFIRM_PAYMENT',amount_minor=0,reference=pay['event_id']))
    report=finance.report(d.store,p,contact_id=c['event_id']);s=report['summaries']['NGN']
    assert (s['sales'],s['received'],s['receivable'])==('10000','2500','7500')
    assert report['balances'][0]['overdue']
    with d.store._connect() as con:assert con.execute("SELECT data_json FROM domain_records WHERE kind=? ORDER BY id",(books.KIND,)).fetchone()[0]==before
    assert finance.report(d.store,p,start='2025-01-01',end='2025-01-02')['record_count']==0
    with pytest.raises(ValueError):finance.report(d.store,p,start='2026-02-01',end='2026-01-01')

def test_contact_retry_line_totals_and_role_validation(dashboard):
    d=dashboard;p=d.credentials['principal'];c=contact(d)
    assert finance.append(d.store,p,c)['status']=='ALREADY_RECORDED'
    with pytest.raises(ValueError):finance.append(d.store,p,{**c,'name':'Changed'})
    sale=detailed(c);sale['details']['items'][0]['amount_minor']=9999
    with pytest.raises(ValueError,match='equal'):books.append(d.store,p,sale)
    supplier=contact(d,'SUPPLIER','Synthetic supplier')
    with pytest.raises(ValueError):books.append(d.store,p,detailed(supplier))

def test_receipts_private_bounded_backup_and_no_ddl(dashboard):
    d=dashboard;p=d.credentials['principal'];s=IdentityService(d.store)
    with d.store._connect() as con:schema=list(map(tuple,con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))
    sale=entry();books.append(d.store,p,sale)
    receipt=dict(operation='receipt',event_id=str(uuid.uuid4()),reference=sale['event_id'],image_base64=base64.b64encode(png()).decode())
    finance.append(d.store,p,receipt);assert finance.append(d.store,p,receipt)['status']=='ALREADY_RECORDED'
    assert request(d,'/api/farm/receipts/'+receipt['event_id'],raw=d.credentials['raw'])[2]==png()
    assert 'image_base64' not in json.dumps(finance.report(d.store,p))
    for role,domain in [('Worker','farming'),('Manager','jobs')]:
        s.create_user(p,'finance-'+role,PASSWORD,role,(domain,));raw,_=s.login('finance-'+role,PASSWORD)
        assert request(d,'/api/farm/finance',raw=raw)[0]==403
        assert request(d,'/api/farm/receipts/'+receipt['event_id'],raw=raw)[0]==403
    with pytest.raises(ValueError):finance.append(d.store,p,{**receipt,'event_id':str(uuid.uuid4()),'image_base64':base64.b64encode(b'<svg/>').decode()})
    args=recovery_point(d.store,d.store.path.parent)
    with sqlite3.connect(args['restore_directory']/'state.sqlite3') as con:
        assert con.execute("SELECT count(*) FROM domain_records WHERE kind=?",(finance.RECEIPTS,)).fetchone()[0]==1
    with d.store._connect() as con:assert schema==list(map(tuple,con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))

def test_financial_browser_contact_invoice_export_and_dashboard(dashboard):
    from playwright.sync_api import sync_playwright,expect
    d=dashboard;c=contact(d);books.append(d.store,d.credentials['principal'],detailed(c))
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work')
            page.get_by_role('button', name='Expand all sections', exact=True).click()
            expect(page.locator('#financeSummary')).to_contain_text('Reported sales: NGN 100.00')
            expect(page.locator('#financeSummary')).to_contain_text('Confirmed money received: NGN 0.00')
            page.get_by_role('button',name='Prepare invoice',exact=True).click()
            expect(page.locator('#financePrint')).to_contain_text(c['name'])
            expect(page.locator('#financePrint')).to_contain_text('NGN 100.00')
            with page.expect_download() as info:page.get_by_role('button',name='Export filtered records (CSV)',exact=True).click()
            assert info.value.suggested_filename=='farm-financial-records.csv'
            assert page.evaluate("()=>financeCsvCell('=HYPERLINK(\"synthetic\")')").startswith('"\'')
            page.fill('#financeSearch','no match');page.get_by_role('button',name='Apply financial filters',exact=True).click()
            expect(page.locator('#financeSummary')).to_contain_text('No financial records')
        finally:browser.close()


def test_period_currency_void_and_export_revision(dashboard):
    d=dashboard;p=d.credentials['principal']
    a=entry(observed_at='2026-01-01T23:30:00Z');books.append(d.store,p,a)
    b=entry(currency='USD',amount_minor=333);books.append(d.store,p,b)
    report=finance.report(d.store,p,start='2026-01-02',end='2026-01-02')
    assert report['summaries']['NGN']['sales']=='10000' and 'USD' not in report['summaries']
    before=report['revision']
    books.append(d.store,p,entry(kind='VOID',reference=a['event_id'],amount_minor=0))
    report=finance.report(d.store,p)
    assert report['revision']!=before and report['summaries']['USD']['sales']=='333'
    assert report['summaries']['NGN']['sales']=='0'


def test_browser_creates_contact_line_item_and_private_receipt(dashboard):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url+'/work')
            page.get_by_role('button', name='Expand all sections', exact=True).click()
            page.get_by_text('Add a customer or supplier',exact=True).click()
            page.fill('#financeContactName','Browser customer');page.get_by_role('button',name='Save contact',exact=True).click()
            expect(page.locator('#financeContactStatus')).to_have_text('Contact saved.')
            page.select_option('#farmMoneyKind','SALE');page.fill('#farmMoneyAmount','123.45')
            page.fill('#farmMoneyObserved','2026-01-01T08:00');page.fill('#farmMoneyReason','Synthetic line item sale')
            page.select_option('#financeCategory','EGG_SALES');page.select_option('#financeContact',label='Browser customer (customer)');page.fill('#financeDue','2026-01-15')
            page.get_by_role('button',name='Add line item',exact=True).click()
            page.locator('.financeLine input[type=text]').fill('Egg delivery');page.locator('.financeLine input[type=number]').fill('123.45')
            page.get_by_role('button',name='Save bookkeeping record',exact=True).click()
            expect(page.locator('#farmMoneyResult')).to_have_text('Saved.')
            expect(page.locator('#financeSummary')).to_contain_text('NGN 123.45')
            page.locator('#financeLedger').get_by_role('button',name='Use as reference',exact=True).click()
            page.get_by_text('Attach a receipt',exact=True).click()
            page.locator('#financeReceiptFile').set_input_files({'name':'synthetic.png','mimeType':'image/png','buffer':png()})
            page.get_by_role('button',name='Save receipt photo',exact=True).click()
            expect(page.locator('#financeReceiptStatus')).to_contain_text('Payment status unchanged')
            page.get_by_role('button',name='View receipt',exact=True).click()
            expect(page.locator('#financeLedger img')).to_have_count(1)
            page.wait_for_function("()=>document.querySelector('#financeLedger img').naturalWidth>0")
        finally:browser.close()
