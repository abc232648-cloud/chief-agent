from domains.farming import brief, bookkeeping, finance
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_bookkeeping import entry
from tests.test_identity_http import request


def test_owner_only_financial_alerts_preserve_manager_bookkeeping(dashboard):
    d=dashboard;owner=d.credentials['principal'];service=IdentityService(d.store)
    service.create_user(owner,'notification-manager',PASSWORD,'Manager',('farming',))
    raw,manager=service.login('notification-manager',PASSWORD)
    sale=entry(details={'category':'OTHER','contact_id':None,'due_on':'2025-01-01','items':[]})
    bookkeeping.append(d.store,manager,sale)
    bookkeeping.append(d.store,manager,entry(kind='PURCHASE_REQUEST'))
    owner_alerts=brief.overview(d.store,owner)['alerts']
    assert {'OVERDUE_FINANCE','PURCHASE_APPROVAL'} <= {a['type'] for a in owner_alerts}
    code,headers,body=request(d,'/api/farm/brief',raw=raw)
    assert code==200 and 'finance' in body
    assert not {'OVERDUE_FINANCE','PURCHASE_APPROVAL'} & {a['type'] for a in body['alerts']}
    assert finance.report(d.store,manager)['summaries']['NGN']['receivable']=='10000'
    assert body['external_notifications']=='NOT_CONFIGURED'
