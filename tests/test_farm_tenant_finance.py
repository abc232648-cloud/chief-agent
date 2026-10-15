"""Finance records and observable metadata stay inside the selected Farm."""
import base64
import io
import json
from types import SimpleNamespace

import pytest

from application import farm_api
from domains import tenancy
from domains.farming import bookkeeping as books, finance, financial_permissions as permissions
from tests.checkpoint_f_fixture import e_base, f_fixture
from tests.test_farm_bookkeeping import entry
from tests.test_farm_photos import png
from tests.test_farm_tenant_lineage import _two_company_fixture


@pytest.fixture
def tenants(f_fixture):
    store, owner, yo, go, yf, gf, ym, gm = _two_company_fixture(f_fixture)
    return SimpleNamespace(store=store, owner=owner, yf=yf, gf=gf, ym=ym, gm=gm)


def contact(event_id='shared-contact', name='Customer'):
    return dict(operation='contact', event_id=event_id, name=name, type='BOTH', contact='')


def receipt(reference, event_id='shared-receipt'):
    return dict(operation='receipt', event_id=event_id, reference=reference,
                image_base64=base64.b64encode(png()).decode())


def test_colliding_ids_contacts_receipts_and_report_metadata(tenants):
    t=tenants
    for actor, farm, amount, name in [(t.ym,t.yf,10000,'Yellow customer'),(t.gm,t.gf,20000,'Green customer')]:
        c=contact(name=name)
        finance.append(t.store,actor,c,farm_id=farm)
        sale=entry(event_id='shared-sale',amount_minor=amount,counterparty=name,
                   details=dict(category='OTHER',contact_id=c['event_id'],due_on=None,items=[]))
        assert books.append(t.store,actor,sale,farm_id=farm)['status']=='RECORDED'
        assert books.append(t.store,actor,sale,farm_id=farm)['status']=='ALREADY_RECORDED'
        finance.append(t.store,actor,receipt('shared-sale'),farm_id=farm)
    yellow=finance.report(t.store,t.ym)
    green=finance.report(t.store,t.gm)
    assert yellow['summaries']['NGN']['sales']=='10000'
    assert green['summaries']['NGN']['sales']=='20000'
    assert yellow['balances'][0]['id']=='shared-sale'
    assert yellow['contacts'][0]['name']=='Yellow customer'
    assert yellow['receipts'][0]['reference']=='shared-sale'
    assert 'Green customer' not in json.dumps(yellow)
    assert 'image_base64' not in json.dumps(yellow)
    assert finance.receipt(t.store,t.ym,'shared-receipt')==png()
    finance.append(t.store,t.gm,contact('green-only-contact','Other green customer'))
    books.append(t.store,t.gm,entry(event_id='green-only-sale'))
    finance.append(t.store,t.gm,receipt('green-only-sale','green-only-receipt'))
    assert finance.report(t.store,t.ym)==yellow
    assert finance.report(t.store,t.gm)['revision']!=green['revision']
    assert {r['payload']['event_id'] for r in finance.linked_history(t.store,t.ym,'shared-sale')['records']}=={'shared-sale'}
    for read, key in [(finance.receipt,'green-only-receipt'),(finance.linked_history,'green-only-sale')]:
        with pytest.raises(FileNotFoundError):read(t.store,t.ym,key)
    for farm in [t.gf,'missing-farm']:
        with pytest.raises(PermissionError):finance.report(t.store,t.ym,farm_id=farm)
    with pytest.raises(ValueError,match='Select a Farm'):finance.report(t.store,t.owner)


@pytest.mark.parametrize('kind,amount', [('PAYMENT_CLAIM',100),('VOID',0),('DISPUTE_DEBT',0)])
def test_cross_farm_references_cannot_mutate_ledger(tenants,kind,amount):
    t=tenants
    books.append(t.store,t.gm,entry(event_id='green-debt'))
    before=finance.report(t.store,t.gm)
    with pytest.raises(ValueError):
        books.append(t.store,t.owner,entry(kind=kind,amount_minor=amount,reference='green-debt',receipt_ref='proof'),farm_id=t.yf)
    assert finance.report(t.store,t.gm)==before


def test_contact_and_receipt_references_and_limits_are_scoped(tenants):
    t=tenants
    finance.append(t.store,t.gm,contact('green-contact'))
    books.append(t.store,t.gm,entry(event_id='green-sale'))
    with pytest.raises(ValueError):
        books.append(t.store,t.ym,entry(counterparty='Customer',details=dict(category='OTHER',contact_id='green-contact',due_on=None,items=[])))
    with pytest.raises(ValueError):finance.append(t.store,t.ym,receipt('green-sale'))
    for actor in (t.ym,t.gm):
        books.append(t.store,actor,entry(event_id='same-sale'))
        for i in range(5):finance.append(t.store,actor,receipt('same-sale',f'receipt-{i}'))
        with pytest.raises(ValueError,match='5 receipts'):finance.append(t.store,actor,receipt('same-sale','receipt-six'))


def test_permissions_do_not_enumerate_or_restrict_other_farms(tenants):
    t=tenants
    view=permissions.overview(t.store,t.owner,farm_id=t.yf)
    assert [u['id'] for u in view['users']]==[t.ym.id]
    p=dict(event_id='permission-change',human_id=t.gm.id,expected_revision=None,permissions={**permissions.DEFAULTS,'access':False})
    with pytest.raises(PermissionError):permissions.configure(t.store,t.owner,p,farm_id=t.yf)
    p['human_id']=t.ym.id
    permissions.configure(t.store,t.owner,p,farm_id=t.yf)
    with pytest.raises(PermissionError):finance.report(t.store,t.ym)
    assert finance.report(t.store,t.gm)['record_count']==0
    assert permissions.overview(t.store,t.owner,farm_id=t.gf)['users'][0]['revision'] is None
    tenancy.append(t.store,t.owner,dict(event_id='assign-second-farm',operation='assign_user_farm',
        assignment_id='second-farm',human_id=t.ym.id,organization_id='org-green',farm_id=t.gf,
        role='GENERAL_MANAGER',reason='Shared manager has separate finance permissions'))
    assert finance.report(t.store,t.ym,farm_id=t.gf)['record_count']==0
    with pytest.raises(PermissionError):finance.report(t.store,t.ym,farm_id=t.yf)


class Handler:
    def __init__(self,path,farm):
        self.path=path;self.headers={'X-Chief-Farm-ID':farm};self.wfile=io.BytesIO();self.status=None;self.payload=None
    def json(self,payload,status=200):self.payload=payload;self.status=status
    def send_response(self,status):self.status=status
    def send_header(self,*args):pass
    def end_headers(self):pass


def test_finance_api_propagates_selection_and_keeps_other_routes_blocked(tenants):
    t=tenants
    def dispatch(path,method='GET',body=None,farm=None,principal=None):
        h=Handler(path,farm or t.yf)
        assert farm_api.dispatch(h,t.store,principal or t.owner,path,method,body)
        return h
    assert dispatch('/api/farm/bookkeeping','POST',entry(event_id='api-sale')).status==200
    assert dispatch('/api/farm/finance','POST',receipt('api-sale','api-receipt')).status==200
    for path in ['/api/farm/finance','/api/farm/bookkeeping','/api/farm/financial-permissions','/api/farm/finance/history/api-sale','/api/farm/receipts/api-receipt']:
        assert dispatch(path).status==200
        with pytest.raises(PermissionError):dispatch(path,principal=t.gm)
    assert dispatch('/api/farm/finance',farm=t.gf).payload['record_count']==0
    assert dispatch('/api/farm/receipts/api-receipt','HEAD').wfile.getvalue()==b''
    assert dispatch('/api/farm/receipts/api-receipt').wfile.getvalue()==png()
    assert dispatch('/api/farm/planning').status==503
    assert dispatch('/api/farm/photos/private-photo').status==503
