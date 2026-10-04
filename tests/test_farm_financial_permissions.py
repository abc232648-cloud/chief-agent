import uuid
import pytest
from domains.farming import financial_permissions as permissions, bookkeeping as books, finance
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_bookkeeping import entry
from tests.test_identity_http import request


def test_owner_toggles_enforced_without_new_login_and_cannot_grant_approval(dashboard):
    d=dashboard;owner=d.credentials['principal'];service=IdentityService(d.store)
    service.create_user(owner,'finance-manager',PASSWORD,'Manager',('farming',))
    _,manager=service.login('finance-manager',PASSWORD)
    debt=entry(kind='OPENING_PAYABLE');books.append(d.store,manager,debt)
    payload={'event_id':str(uuid.uuid4()),'human_id':manager.id,'expected_revision':None,'permissions':{**permissions.DEFAULTS,'flag_debts':False}}
    with pytest.raises(PermissionError):permissions.configure(d.store,manager,payload)
    permissions.configure(d.store,owner,payload)
    assert permissions.configure(d.store,owner,payload)['status']=='ALREADY_RECORDED'
    dispute=entry(kind='DISPUTE_DEBT',amount_minor=0,reference=debt['event_id'])
    with pytest.raises(PermissionError):books.append(d.store,manager,dispute)
    payload={**payload,'event_id':str(uuid.uuid4()),'expected_revision':payload['event_id'],'permissions':dict(permissions.DEFAULTS)}
    permissions.configure(d.store,owner,payload);books.append(d.store,manager,dispute)
    with pytest.raises(PermissionError):books.append(d.store,manager,entry(kind='RESOLVE_DEBT_DISPUTE',amount_minor=0,reference=dispute['event_id']))
    payload={**payload,'event_id':str(uuid.uuid4()),'expected_revision':payload['event_id'],'permissions':{**permissions.DEFAULTS,'access':False}}
    permissions.configure(d.store,owner,payload)
    with pytest.raises(PermissionError):finance.report(d.store,manager)
    assert finance.report(d.store,owner)['summaries']['NGN']['payable']=='10000'
    with pytest.raises(ValueError,match='changed'):
        permissions.configure(d.store,owner,{**payload,'event_id':str(uuid.uuid4())})


def test_financial_permission_page_and_manager_endpoint_denial(dashboard):
    from playwright.sync_api import sync_playwright, expect
    d=dashboard;owner=d.credentials['principal'];service=IdentityService(d.store)
    service.create_user(owner,'toggle-manager',PASSWORD,'Manager',('farming',))
    raw,_=service.login('toggle-manager',PASSWORD)
    assert request(d,'/api/farm/financial-permissions',raw=raw)[0]==403
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url+'/work')
            page.get_by_role('button',name='Expand all sections',exact=True).click()
            root=page.locator('#farmFinancialPermissions')
            expect(root).to_contain_text('toggle-manager')
            root.get_by_label('Flag debt disputes',exact=True).uncheck()
            root.get_by_role('button',name='Save permissions for toggle-manager',exact=True).click()
            expect(root).to_contain_text('Permissions saved.')
            assert permissions.overview(d.store,owner)['users'][0]['permissions']['flag_debts'] is False
        finally:browser.close()
