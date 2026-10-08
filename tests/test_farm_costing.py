import pytest
from domains.farming import bookkeeping as books, costing, journal
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_bookkeeping import entry
from tests.test_farm_journal import record


def report(d, **kw):
    return costing.report(d.store,d.credentials['principal'],**{'start':'2026-01-01','end':'2026-01-01','currency':'NGN','feed_minor_per_kg':200,**kw})


def test_costs_use_consumption_and_incurred_categories_not_cash(dashboard):
    d=dashboard;p=d.credentials['principal']
    for kind,amount in [('feed_opening','100'),('feed_received','50'),('feed_used','2.125'),('eggs_collected','10')]:
        journal.append(d.store,p,record(kind=kind,quantity=amount))
    for category,amount in [('FEED',10000),('BIRDS',20000),('WAGES',100),('OTHER',30)]:
        books.append(d.store,p,entry(kind='EXPENSE_CLAIM',amount_minor=amount,details={'category':category,'contact_id':None,'due_on':None,'items':[]}))
    r=report(d)
    assert r['consumed_feed_cost_minor']=='425.000'
    assert r['cost_per_collected_egg_minor']=='52.5000'
    assert r['feed_purchases_minor']=='10000' and r['bird_acquisition_minor']=='20000'
    assert r['unclassified_expenses_minor']=='30'
    assert r['operating_expenses_minor']==r['recurring_expenses_minor']=='100'
    assert r['expense_recurrence']=='NOT_ESTABLISHED'
    assert r['saleable_eggs'] is None and r['authority']=='NONE'
    assert r['status']=='PROVISIONAL'


def test_unknown_zero_and_corrected_sources_are_explicit(dashboard):
    d=dashboard;p=d.credentials['principal']
    assert report(d)['cost_per_collected_egg_minor'] is None
    journal.append(d.store,p,record(kind='feed_opening',quantity='10'))
    used=record(kind='feed_used',quantity='2');journal.append(d.store,p,used)
    journal.append(d.store,p,record(kind='eggs_collected',quantity='0'))
    before=report(d)
    assert before['cost_per_collected_egg_minor'] is None
    corrected=record(kind='feed_used',quantity='1',corrects=used['event_id'],reason='Corrected scale')
    journal.append(d.store,p,corrected)
    after=report(d)
    assert after['revision']!=before['revision']
    assert used['event_id'] not in after['source_references']
    assert corrected['event_id'] in after['source_references']
    assert report(d,feed_minor_per_kg=None)['recorded_recurring_cost_minor'] is None


def test_costing_lagos_boundary_and_worker_denial(dashboard):
    d=dashboard;p=d.credentials['principal']
    journal.append(d.store,p,record(kind='eggs_collected',quantity='5',observed_at='2025-12-31T23:30:00Z'))
    assert report(d)['collected_eggs']==5
    service=IdentityService(d.store);service.create_user(p,'cost-worker',PASSWORD,'Worker',('farming',))
    _,worker=service.login('cost-worker',PASSWORD)
    with pytest.raises(PermissionError):
        costing.report(d.store,worker,start='2026-01-01',end='2026-01-01',currency='NGN')
    for value in [True,-1,1.5,'200']:
        with pytest.raises(ValueError):report(d,feed_minor_per_kg=value)
