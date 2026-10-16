import uuid
import pytest
from domains.farming import health_records as health, setup, bookkeeping as books
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_setup import entity
from tests.test_farm_bookkeeping import entry
from tests.test_identity_http import request


def flock(d, name='Flock 3'):
    owner = d.credentials['principal']
    house = {**entity(), 'entity_type': 'HOUSE', 'name': 'House ' + str(uuid.uuid4())[:6]}
    setup.append(d.store, owner, house)
    f = {**entity(), 'entity_type': 'FLOCK', 'name': name, 'house_id': house['entity_id']}
    setup.append(d.store, owner, f)
    return f['entity_id']


def health_event(d, fid, *, observed_at='2026-01-01T08:00:00+01:00', kind='MEDICINE'):
    owner = d.credentials['principal']
    ev = dict(event_id=str(uuid.uuid4()), kind=kind, entity_id=fid,
              observed_at=observed_at, summary='Administered recorded product',
              product='Tylosin Tartrate', quantity='50', unit='g', administered_by='Dr. Adebayo',
              batch='BATCH-01', expires_on='2027-01-01', source_reference='RX-9921',
              instructions='5 days withdrawal for eggs', instruction_source='Veterinary Prescription',
              corrects=None, reason='')
    health.append(d.store, owner, ev)
    return ev['event_id']


def restriction(fid, hid, **kw):
    base = dict(event_id=str(uuid.uuid4()), health_event_id=hid, entity_id=fid,
                output_type='EGGS', restriction_type='WITHDRAWAL',
                active_from='2026-01-01T08:00:00+01:00', active_until='2026-01-06T08:00:00+01:00',
                reason='Recorded antibiotic egg withdrawal hold', source_document='RX-9921 Vet Prescription',
                reviewed_by='Dr. Adebayo DVM')
    base.update(kw)
    return base


def sale_details(fid, **kw):
    value={'category':'EGG_SALES','contact_id':None,'due_on':None,'items':[],
           'flock_id':fid,'output_type':'EGGS'}
    value.update(kw)
    return value


def test_record_treatment_instruction_is_human_reported_not_auto_qualified(dashboard):
    d=dashboard;owner=d.credentials['principal'];fid=flock(d);hid=health_event(d,fid)
    res=restriction(fid,hid)
    assert health.append_restriction(d.store,owner,res)['status']=='RECORDED'
    assert health.append_restriction(d.store,owner,res)['status']=='ALREADY_RECORDED'
    item=health.restrictions_overview(d.store,owner,entity_id=fid)['items'][0]
    assert item['payload']['health_event_id']==hid
    assert item['evidence_status']=='HUMAN_REPORTED_INSTRUCTION'
    assert item['duration_known'] is True


def test_restriction_requires_current_treatment_event_and_matching_flock(dashboard):
    d=dashboard;owner=d.credentials['principal'];fid=flock(d);hid=health_event(d,fid)
    with pytest.raises(ValueError,match='Referenced health event'):
        health.append_restriction(d.store,owner,restriction(fid,str(uuid.uuid4())))
    other=flock(d,'Other flock')
    with pytest.raises(ValueError,match='Restriction flock does not match'):
        health.append_restriction(d.store,owner,restriction(other,hid))

    observation=dict(event_id=str(uuid.uuid4()),kind='OBSERVATION',entity_id=fid,
                     observed_at='2026-01-02T08:00:00+01:00',summary='Observed birds',
                     product='',quantity=None,unit='',administered_by='',batch='',expires_on=None,
                     source_reference='',instructions='',instruction_source='',corrects=None,reason='')
    health.append(d.store,owner,observation)
    with pytest.raises(ValueError,match='medicine or vaccination'):
        health.append_restriction(d.store,owner,restriction(fid,observation['event_id'],active_from='2026-01-02T08:00:00+01:00'))


def test_medicine_automatically_fails_closed_until_instruction_is_recorded(dashboard):
    d=dashboard;fid=flock(d);health_event(d,fid)
    with d.store._connect() as con:
        status=health.check_restriction(con,fid,output_type='EGGS',at_time='2026-01-03T10:00:00+01:00')
        assert status['restricted'] is True
        assert status['duration_known'] is False
        assert status['active_holds'][0]['automatic'] is True
        assert 'unknown or unverified' in status['warning']


def test_unknown_duration_fails_closed_and_latest_instruction_can_replace_it(dashboard):
    d=dashboard;owner=d.credentials['principal'];fid=flock(d);hid=health_event(d,fid)
    health.append_restriction(d.store,owner,restriction(fid,hid,active_until=None))
    with d.store._connect() as con:
        assert health.check_restriction(con,fid,at_time='2030-01-01T00:00:00Z')['restricted'] is True
    # A later human-recorded instruction for the same treatment/output supersedes
    # the earlier unknown-duration instruction rather than creating a permanent lock.
    health.append_restriction(d.store,owner,restriction(fid,hid,event_id=str(uuid.uuid4()),active_until='2026-01-06T08:00:00+01:00'))
    with d.store._connect() as con:
        assert health.check_restriction(con,fid,at_time='2026-01-03T00:00:00+01:00')['restricted'] is True
        assert health.check_restriction(con,fid,at_time='2026-01-07T00:00:00+01:00')['restricted'] is False


def test_separate_treatments_are_additive_and_one_clearance_cannot_clear_another(dashboard):
    d=dashboard;owner=d.credentials['principal'];fid=flock(d)
    first=health_event(d,fid,observed_at='2026-01-01T08:00:00+01:00')
    second=health_event(d,fid,observed_at='2026-01-02T08:00:00+01:00')
    health.append_restriction(d.store,owner,restriction(fid,first,active_until='2026-01-03T08:00:00+01:00'))
    with d.store._connect() as con:
        status=health.check_restriction(con,fid,at_time='2026-01-04T00:00:00+01:00')
        assert status['restricted'] is True
        assert any(h.get('health_event_id')==second and h.get('automatic') for h in status['active_holds'])


def test_sale_blocked_during_hold_and_allowed_after_recorded_clearance(dashboard):
    d=dashboard;owner=d.credentials['principal'];fid=flock(d);hid=health_event(d,fid)
    health.append_restriction(d.store,owner,restriction(fid,hid,active_until='2026-01-06T08:00:00+01:00'))
    blocked=entry(kind='SALE',observed_at='2026-01-03T10:00:00+01:00',details=sale_details(fid))
    with pytest.raises(PermissionError,match='Active Treatment Hold'):
        books.append(d.store,owner,blocked)
    cleared=entry(kind='SALE',observed_at='2026-01-08T10:00:00+01:00',details=sale_details(fid))
    assert books.append(d.store,owner,cleared)['status']=='RECORDED'


@pytest.mark.parametrize('variant',['missing_details','missing_flock','unknown_flock','invalid_output','other_category'])
def test_treatment_sale_bypass_paths_are_rejected(dashboard,variant):
    d=dashboard;owner=d.credentials['principal'];fid=flock(d);health_event(d,fid)
    sale=entry(kind='SALE',observed_at='2026-01-03T10:00:00+01:00',details=sale_details(fid))
    if variant=='missing_details':
        sale.pop('details')
    elif variant=='missing_flock':
        sale['details'].pop('flock_id')
    elif variant=='unknown_flock':
        sale['details']['flock_id']='unregistered-flock'
    elif variant=='invalid_output':
        sale['details']['output_type']='OTHER'
    else:
        sale['details']={'category':'OTHER','contact_id':None,'due_on':None,'items':[]}
    with pytest.raises((ValueError,PermissionError)):
        books.append(d.store,owner,sale)


def test_product_sale_category_controls_output_and_conflicting_provenance_is_rejected(dashboard):
    d=dashboard;owner=d.credentials['principal'];fid=flock(d);other=flock(d,'Other flock')
    bad=entry(kind='SALE',details=sale_details(fid,entity_id=other))
    with pytest.raises(ValueError,match='one source flock'):
        books.append(d.store,owner,bad)
    bird=entry(kind='SALE',details={'category':'BIRD_SALES','contact_id':None,'due_on':None,'items':[],
                                   'flock_id':fid,'output_type':'EGGS'})
    with pytest.raises(ValueError,match='cannot be overridden'):
        books.append(d.store,owner,bird)


def test_legacy_generic_sale_still_records_when_no_treatment_hold_exists(dashboard):
    d=dashboard;owner=d.credentials['principal']
    assert books.append(d.store,owner,entry(kind='SALE'))['status']=='RECORDED'


def test_worker_cannot_self_issue_or_release_restriction(dashboard):
    d=dashboard;owner=d.credentials['principal'];fid=flock(d);hid=health_event(d,fid)
    svc=IdentityService(d.store);svc.create_user(owner,'pen-worker',PASSWORD,'Worker',('farming',))
    _,worker=svc.login('pen-worker',PASSWORD)
    with pytest.raises(PermissionError):health.append_restriction(d.store,worker,restriction(fid,hid))


def test_http_treatment_restrictions_endpoint(dashboard):
    d=dashboard;owner=d.credentials['principal'];fid=flock(d);hid=health_event(d,fid);raw=d.credentials['raw']
    res=restriction(fid,hid)
    code,_,body=request(d,'/api/farm/treatment-restrictions',method='POST',body=res,raw=raw)
    assert code==200 and body['status']=='RECORDED'
    code,_,body=request(d,f'/api/farm/treatment-restrictions?flock_id={fid}',raw=raw)
    assert code==200 and body['total']==1
