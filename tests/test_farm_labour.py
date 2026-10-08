import uuid
import pytest
from domains.farming import labour,bookkeeping as books,finance
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_bookkeeping import entry


def work(**kw):return {'event_id':str(uuid.uuid4()),'operation':'WORK','person':'Synthetic helper','task':'Cleaned store','time_basis':'DAYS','work_date':'2026-01-01','started_at':None,'ended_at':None,'days':'1','currency':'NGN','agreed_total_minor':500000,'corrects':None,'reason':'Reported work',**kw}


def test_work_expense_link_no_double_count_or_implied_payment(dashboard):
    d=dashboard;p=d.credentials['principal'];w=work();labour.append(d.store,p,w)
    assert labour.append(d.store,p,w)['status']=='ALREADY_RECORDED'
    assert finance.report(d.store,p)['summaries']=={}
    expense=entry(kind='EXPENSE_CLAIM',counterparty=w['person'],amount_minor=500000,details={'category':'WAGES','contact_id':None,'due_on':None,'items':[]});books.append(d.store,p,expense)
    link={'event_id':str(uuid.uuid4()),'operation':'LINK_EXPENSE','work_id':w['event_id'],'expense_id':expense['event_id'],'reason':'Matched wage record'}
    labour.append(d.store,p,link)
    assert finance.report(d.store,p)['summaries']['NGN']['expenses']=='500000'
    assert finance.report(d.store,p)['summaries']['NGN']['paid']=='0'
    assert labour.overview(d.store,p)['items'][0]['expense_status']=='LINKED'
    with pytest.raises(ValueError):labour.append(d.store,p,{**link,'event_id':str(uuid.uuid4())})
    books.append(d.store,p,entry(kind='VOID',amount_minor=0,reference=expense['event_id']))
    assert labour.overview(d.store,p)['items'][0]['expense_status']=='NEEDS_REVIEW'


def test_times_corrections_and_worker_privacy(dashboard):
    d=dashboard;p=d.credentials['principal'];w=work(time_basis='TIMED',days=None,started_at='2026-01-01T08:00:00Z',ended_at='2026-01-01T12:00:00Z',agreed_total_minor=None)
    labour.append(d.store,p,w);labour.append(d.store,p,{**w,'event_id':str(uuid.uuid4()),'corrects':w['event_id'],'reason':'Corrected task','task':'Cleaned and repaired store'})
    assert [r['is_current'] for r in labour.overview(d.store,p)['items']]==[True,False]
    with pytest.raises(ValueError):labour.append(d.store,p,work(time_basis='TIMED',days=None,started_at='2026-01-01T12:00:00Z',ended_at='2026-01-01T08:00:00Z'))
    service=IdentityService(d.store);service.create_user(p,'labour-worker',PASSWORD,'Worker',('farming',));_,worker=service.login('labour-worker',PASSWORD)
    with pytest.raises(PermissionError):labour.overview(d.store,worker)


def test_browser_labour_entry(dashboard):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url+'/work');page.get_by_role('button',name='Expand all sections',exact=True).click()
            for id,value in [('farmLabourPerson','Synthetic helper'),('farmLabourTask','Cleaning'),('farmLabourDate','2026-01-01'),('farmLabourDays','1'),('farmLabourReason','Work register')]:page.fill('#'+id,value)
            page.get_by_role('button',name='Save labour record',exact=True).click()
            expect(page.locator('#farmLabourStatus')).to_have_text('Labour recorded. No expense or payment created.')
            expect(page.locator('#farmLabourHistory')).to_contain_text('Agreed pay: Unknown')
        finally:browser.close()


def test_browser_correction_then_expense_link_preserves_cost(dashboard):
    from playwright.sync_api import sync_playwright,expect
    d=dashboard;p=d.credentials['principal'];w=work();labour.append(d.store,p,w)
    expense=entry(kind='EXPENSE_CLAIM',counterparty=w['person'],amount_minor=500000,details={'category':'WAGES','contact_id':None,'due_on':None,'items':[]});books.append(d.store,p,expense)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work');page.get_by_role('button',name='Expand all sections',exact=True).click()
            page.get_by_role('button',name='Correct labour record',exact=True).click()
            page.fill('#farmLabourTask','Cleaned and swept store');page.fill('#farmLabourReason','Corrected task description')
            page.get_by_role('button',name='Save labour correction',exact=True).click()
            expect(page.locator('#farmLabourStatus')).to_contain_text('Original retained')
            page.get_by_role('button',name='Link existing wages expense',exact=True).click()
            page.get_by_label('Reason for linking',exact=True).fill('Checked agreed wages')
            page.get_by_role('button',name='Confirm expense link',exact=True).click()
            expect(page.locator('#farmLabourStatus')).to_have_text('Existing expense linked. No additional cost or payment created.')
            expect(page.locator('#farmLabourHistory')).to_contain_text('Expense linked')
            expect(page.get_by_role('button',name='Correct labour record',exact=True)).to_have_count(0)
            assert len(labour.overview(d.store,p)['items'])==2
            assert finance.report(d.store,p)['summaries']['NGN']['expenses']=='500000'
            page.get_by_label('Reason for removing expense link',exact=True).fill('Review and correct work description')
            page.get_by_role('button',name='Remove expense link',exact=True).click()
            expect(page.locator('#farmLabourStatus')).to_contain_text('Expense and payment history unchanged')
            expect(page.get_by_role('button',name='Correct labour record',exact=True)).to_have_count(1)
            assert finance.report(d.store,p)['summaries']['NGN']['expenses']=='500000'
        finally:browser.close()


def test_browser_correction_waits_for_record_refresh(dashboard):
    """A slow refresh must not expose old actionable rows after a save."""
    from playwright.sync_api import sync_playwright, expect
    d = dashboard
    labour.append(d.store, d.credentials['principal'], work())
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.add_init_script("""(() => {
              const original = window.fetch;
              window.fetch = async (...args) => {
                const response = await original(...args);
                if (window.holdLabourRefresh && String(args[0]).includes('/api/farm/labour?')) {
                  window.labourRefreshHeld = true;
                  await new Promise(resolve => { window.releaseLabourRefresh = resolve; });
                }
                return response;
              };
            })();""")
            page.goto(d.url + '/work')
            page.get_by_role('button', name='Expand all sections', exact=True).click()
            page.get_by_role('button', name='Correct labour record', exact=True).click()
            page.fill('#farmLabourTask', 'Corrected synthetic task')
            page.fill('#farmLabourReason', 'Checked work register')
            page.evaluate('window.holdLabourRefresh = true')
            page.get_by_role('button', name='Save labour correction', exact=True).click()
            page.wait_for_function('() => window.labourRefreshHeld === true')
            assert page.locator('#farmLabourHistory').evaluate('(element) => element.inert')
            expect(page.locator('#farmLabourStatus')).not_to_contain_text('Correction recorded.')
            expect(page.locator('#farmLabourHistory')).not_to_contain_text('Corrected synthetic task')
            page.evaluate('window.holdLabourRefresh = false; window.releaseLabourRefresh()')
            expect(page.locator('#farmLabourStatus')).to_contain_text('Correction recorded.')
            expect(page.locator('#farmLabourHistory')).to_contain_text('Corrected synthetic task')
            assert not page.locator('#farmLabourHistory').evaluate('(element) => element.inert')
        finally:
            browser.close()


def test_manager_cannot_link_and_mismatched_expense_is_rejected(dashboard):
    d=dashboard;p=d.credentials['principal'];w=work();labour.append(d.store,p,w)
    service=IdentityService(d.store);service.create_user(p,'labour-manager',PASSWORD,'Manager',('farming',));_,manager=service.login('labour-manager',PASSWORD)
    expense=entry(kind='EXPENSE_CLAIM',counterparty='Different person',amount_minor=500000,details={'category':'WAGES','contact_id':None,'due_on':None,'items':[]});books.append(d.store,p,expense)
    link={'event_id':str(uuid.uuid4()),'operation':'LINK_EXPENSE','work_id':w['event_id'],'expense_id':expense['event_id'],'reason':'Check'}
    with pytest.raises(PermissionError):labour.append(d.store,manager,link)
    with pytest.raises(ValueError,match='match'):labour.append(d.store,p,link)


def test_unlink_retry_stale_request_and_relink_preserve_bookkeeping(dashboard):
    d=dashboard;p=d.credentials['principal'];w=work();labour.append(d.store,p,w)
    expense=entry(kind='EXPENSE_CLAIM',counterparty=w['person'],amount_minor=500000,details={'category':'WAGES','contact_id':None,'due_on':None,'items':[]});books.append(d.store,p,expense)
    link={'event_id':str(uuid.uuid4()),'operation':'LINK_EXPENSE','work_id':w['event_id'],'expense_id':expense['event_id'],'reason':'Checked'}
    labour.append(d.store,p,link)
    unlink={'event_id':str(uuid.uuid4()),'operation':'UNLINK_EXPENSE','link_id':link['event_id'],'reason':'Wrong work description'}
    svc=IdentityService(d.store);svc.create_user(p,'unlink-manager',PASSWORD,'Manager',('farming',));_,manager=svc.login('unlink-manager',PASSWORD)
    with pytest.raises(PermissionError):labour.append(d.store,manager,unlink)
    assert labour.append(d.store,p,unlink)['status']=='RECORDED'
    assert labour.append(d.store,p,unlink)['status']=='ALREADY_RECORDED'
    corrected={**w,'event_id':str(uuid.uuid4()),'corrects':w['event_id'],'task':'Correct task'}
    labour.append(d.store,p,corrected)
    new_link={**link,'event_id':str(uuid.uuid4()),'work_id':corrected['event_id']};labour.append(d.store,p,new_link)
    with pytest.raises(ValueError):labour.append(d.store,p,{**unlink,'event_id':str(uuid.uuid4())})
    rows=labour.overview(d.store,p)['items']
    assert rows[0]['expense_link_id']==new_link['event_id']
    assert [x['payload']['operation'] for x in rows[1]['link_history']]==['LINK_EXPENSE','UNLINK_EXPENSE']
    assert finance.report(d.store,p)['summaries']['NGN']['expenses']=='500000'
    assert finance.report(d.store,p)['summaries']['NGN']['paid']=='0'
