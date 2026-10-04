import pytest
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_identity_http import request
from tests.test_farm_setup import assign
from tests.test_farm_journal import record
from domains.farming import journal


@pytest.mark.parametrize('chief_role,farm_role,setup_visible,finance_visible',[
    ('Worker','WORKER',False,False),('Worker','SUPERVISOR',False,False),
    ('Manager','GENERAL_MANAGER',True,True)])
def test_farm_role_portal_has_no_chief_shell(dashboard,chief_role,farm_role,setup_visible,finance_visible):
    from playwright.sync_api import sync_playwright,expect
    d=dashboard;service=IdentityService(d.store);owner=d.credentials['principal']
    uid=service.create_user(owner,'farm-person',PASSWORD,chief_role,('farming',))
    assign(d.store,owner,uid,farm_role)
    raw,principal=service.login('farm-person',PASSWORD)
    for path in ['/chief','/dashboard.html','/api/ui/overview','/api/ui/models','/api/ui/runtime','/api/ui/evidence/farming']:
        assert request(d,path,raw=raw)[0]==403
    journal.append(d.store,owner,record())
    if farm_role=='WORKER':
        assert journal.overview(d.store,principal)['records']==[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.context.add_cookies([{'name':'chief_session','value':raw,'url':d.url,'httpOnly':True,'sameSite':'Strict'}])
            page.goto(d.url)
            expect(page).to_have_url(d.url+'/work')
            expect(page.locator('#workRole')).to_have_text(farm_role.replace('_',' '))
            assert page.locator('#chiefAgents,#models,#runtime,#components,#settings,#sidebar').count()==0
            for selector,visible in [('#farmSetupCard',setup_visible),('#farmFinanceCard',finance_visible),('#farmRoleCard',False)]:
                if visible:expect(page.locator(selector)).to_be_visible()
                else:expect(page.locator(selector)).to_be_hidden()
            expect(page.locator('#workNavigation')).not_to_contain_text('Job work')
            if farm_role=='WORKER':expect(page.locator('#farmBalances')).to_be_hidden()
            assert page.evaluate("async()=> (await fetch('/api/farm/bookkeeping')).status")==(200 if finance_visible else 403)
            page.locator('#workTitle').click()
            expect(page.locator('#workHome')).to_be_visible()
            assert page.locator('#sidebar').count()==0
            assert page.url==d.url+'/work'
        finally:browser.close()


def test_owner_retains_chief_and_unauthenticated_portal_denied(dashboard):
    assert request(dashboard,'/api/ui/overview',raw=dashboard.credentials['raw'])[0]==200
    assert request(dashboard,'/work')[0]==303
    assert request(dashboard,'/work')[1]['Location']=='/work-login'


def test_farm_role_downgrade_revokes_worker_account_management(dashboard):
    d=dashboard;service=IdentityService(d.store);owner=d.credentials['principal']
    uid=service.create_user(owner,'farm-manager',PASSWORD,'Manager',('farming',))
    raw,_=service.login('farm-manager',PASSWORD)
    body={'username':'managed-worker','password':PASSWORD,'role':'Worker','domains':['farming']}
    assert request(d,'/api/auth/users','POST',body,raw)[0]==200
    assign(d.store,owner,uid,'SUPERVISOR')
    assert request(d,'/api/auth/users','POST',{**body,'username':'forbidden-worker'},raw)[0]==403
    context=request(d,'/api/ui/context',raw=raw)[2]
    assert context['user_management']['roles']==[]
    assert request(d,'/api/auth/users',raw=raw)[2]['users']==[]


def test_legacy_domain_endpoint_and_worker_cannot_read_private_farm_records(dashboard):
    from domains.farming import bookkeeping
    from tests.test_farm_bookkeeping import entry
    from domains.storage import DomainStorage
    from application.composition import default_registry
    from security.permissions import worker_context
    d=dashboard;owner=d.credentials['principal'];service=IdentityService(d.store)
    service.create_user(owner,'legacy-reader',PASSWORD,'Worker',('farming',))
    raw,_=service.login('legacy-reader',PASSWORD)
    bookkeeping.append(d.store,owner,entry(counterparty='PRIVATE_FINANCE_LEGACY'))
    journal.append(d.store,owner,record(notes='PRIVATE_JOURNAL_LEGACY'))
    code,_,body=request(d,'/api/domains/farming',raw=raw)
    assert code==200 and body['records']==[]
    _,agent=default_registry().resolve('farming')
    with worker_context(agent) as context:
        storage=DomainStorage(d.store,context)
        assert storage.overview()['records']==[]
        for kind in ('farm_setup_v1','farm_bookkeeping_v1','poultry_journal_v1','farm_photo_v1','future_private_kind'):
            with pytest.raises(PermissionError):storage.record(kind,{'forged':'record'})
