"""HTTP/browser attack reproductions for the expanded financial boundary."""
import base64,json,uuid
import pytest
from domains.farming import finance,bookkeeping
from identity.service import IdentityService
from tests.test_identity_http import request
from tests.test_farm_bookkeeping import entry
from tests.test_farm_photos import png
from tests.checkpoint_f_fixture import PASSWORD

def test_forged_finance_authority_and_csrf_do_not_write(dashboard):
    d=dashboard;s=IdentityService(d.store);p=d.credentials['principal']
    body=dict(operation='contact',event_id=str(uuid.uuid4()),name='Synthetic attacker',type='BOTH',contact='')
    for role,domain in [('Worker','farming'),('Manager','jobs')]:
        s.create_user(p,'attack-'+role,PASSWORD,role,(domain,));raw,_=s.login('attack-'+role,PASSWORD)
        assert request(d,'/api/farm/finance?role=Owner','POST',body,raw)[0]==403
    assert request(d,'/api/farm/finance','POST',{**body,'actor_id':p.id},d.credentials['raw'])[0]==400
    for extra in ({'csrf':False},{'extra':{'Origin':'https://attacker.invalid'}},{'extra':{'Host':'attacker.invalid'}}):
        assert request(d,'/api/farm/finance','POST',body,d.credentials['raw'],**extra)[0]==403
    assert finance.report(d.store,p)['contacts']==[]

def test_receipt_ids_paths_duplicate_limits_and_confirmation(dashboard):
    d=dashboard;p=d.credentials['principal'];sale=entry();bookkeeping.append(d.store,p,sale)
    body=dict(operation='receipt',event_id=str(uuid.uuid4()),reference=sale['event_id'],image_base64=base64.b64encode(png()).decode())
    for _ in range(5):finance.append(d.store,p,{**body,'event_id':str(uuid.uuid4())})
    with pytest.raises(ValueError,match='At most'):finance.append(d.store,p,body)
    assert request(d,'/api/farm/receipts/..%2F..%2Fconfig')[0]==401
    assert request(d,'/api/farm/receipts/..%2F..%2Fconfig',raw=d.credentials['raw'])[0] in {400,404}
    result=finance.report(d.store,p)
    assert result['summaries']['NGN']['received']=='0'
    assert 'image_base64' not in json.dumps(result)

def test_stored_financial_markup_is_inert(dashboard):
    from playwright.sync_api import sync_playwright,expect
    d=dashboard;attack='<img src=x onerror="window.financePwned=1">'
    bookkeeping.append(d.store,d.credentials['principal'],entry(counterparty=attack,reason=attack))
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work')
            page.get_by_role('button', name='Expand all sections', exact=True).click()
            expect(page.locator('#financeLedger')).to_contain_text(attack)
            assert page.locator('#financeLedger img').count()==0
            page.get_by_role('button',name='Prepare invoice',exact=True).click()
            expect(page.locator('#financePrint')).to_contain_text(attack)
            assert page.locator('#financePrint img').count()==0
            assert page.evaluate('window.financePwned===undefined')
        finally:browser.close()
