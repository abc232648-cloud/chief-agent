import uuid
import pytest
from domains.farming import health_records as health, setup, bookkeeping as books
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_setup import entity
from tests.test_farm_bookkeeping import entry
from tests.test_identity_http import request


def flock(d):
    owner = d.credentials['principal']
    house = {**entity(), 'entity_type': 'HOUSE', 'name': 'House A'}
    setup.append(d.store, owner, house)
    f = {**entity(), 'entity_type': 'FLOCK', 'name': 'Flock 3', 'house_id': house['entity_id']}
    setup.append(d.store, owner, f)
    return f['entity_id']


def health_event(d, fid):
    owner = d.credentials['principal']
    ev = dict(event_id=str(uuid.uuid4()), kind='MEDICINE', entity_id=fid,
              observed_at='2026-01-01T08:00:00+01:00', summary='Administered Tylosin antibiotic',
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
                reason='Antibiotic egg withdrawal hold', source_document='RX-9921 Vet Prescription',
                reviewed_by='Dr. Adebayo DVM')
    base.update(kw)
    return base


def test_record_additive_treatment_restriction_linked_to_health_event(dashboard):
    d = dashboard; owner = d.credentials['principal']
    fid = flock(d)
    hid = health_event(d, fid)
    res = restriction(fid, hid)
    assert health.append_restriction(d.store, owner, res)['status'] == 'RECORDED'
    assert health.append_restriction(d.store, owner, res)['status'] == 'ALREADY_RECORDED'
    
    overview = health.restrictions_overview(d.store, owner, entity_id=fid)
    assert overview['total'] == 1
    item = overview['items'][0]
    assert item['payload']['health_event_id'] == hid
    assert item['payload']['output_type'] == 'EGGS'
    assert item['evidence_status'] == 'QUALIFIED_INSTRUCTION'
    assert item['duration_known'] is True


def test_restriction_rejects_missing_health_event_or_mismatched_flock(dashboard):
    d = dashboard; owner = d.credentials['principal']
    fid = flock(d)
    hid = health_event(d, fid)
    
    # Missing health event
    with pytest.raises(ValueError, match='Referenced health event does not exist'):
        health.append_restriction(d.store, owner, restriction(fid, str(uuid.uuid4())))
    
    # Mismatched flock
    other_fid = flock(d)
    with pytest.raises(ValueError, match='Restriction flock does not match'):
        health.append_restriction(d.store, owner, restriction(other_fid, hid))


def test_unknown_duration_fails_closed_and_never_clears_automatically(dashboard):
    d = dashboard; owner = d.credentials['principal']
    fid = flock(d)
    hid = health_event(d, fid)
    # active_until is None -> duration unknown
    res = restriction(fid, hid, active_until=None)
    health.append_restriction(d.store, owner, res)
    
    with d.store._connect() as con:
        status = health.check_restriction(con, fid, output_type='EGGS', at_time='2030-01-01T00:00:00Z')
        assert status['restricted'] is True
        assert status['duration_known'] is False
        assert 'medication clearing period is unknown and unverified' in status['warning']
        assert 'Flock 3' in status['warning']


def test_overlapping_restrictions_require_all_periods_cleared(dashboard):
    d = dashboard; owner = d.credentials['principal']
    fid = flock(d)
    hid = health_event(d, fid)
    
    # Hold 1: active until Jan 06
    health.append_restriction(d.store, owner, restriction(fid, hid, active_until='2026-01-06T08:00:00+01:00'))
    # Hold 2: active until Jan 10
    health.append_restriction(d.store, owner, restriction(fid, hid, active_until='2026-01-10T08:00:00+01:00', event_id=str(uuid.uuid4())))
    
    with d.store._connect() as con:
        # Before Jan 10: still restricted
        status = health.check_restriction(con, fid, output_type='EGGS', at_time='2026-01-08T00:00:00+01:00')
        assert status['restricted'] is True
        assert 'cannot be sold until Jan 10' in status['warning']
        
        # After Jan 10: cleared
        status_cleared = health.check_restriction(con, fid, output_type='EGGS', at_time='2026-01-11T00:00:00+01:00')
        assert status_cleared['restricted'] is False


def test_sales_blocked_during_active_restriction_with_farm_guidance_warning(dashboard):
    d = dashboard; owner = d.credentials['principal']
    fid = flock(d)
    hid = health_event(d, fid)
    health.append_restriction(d.store, owner, restriction(fid, hid, active_until='2026-01-06T08:00:00+01:00'))
    
    sale_restricted = entry(kind='SALE', observed_at='2026-01-03T10:00:00+01:00',
                            details={'category': 'EGG_SALES', 'contact_id': None, 'due_on': None, 'items': [],
                                     'flock_id': fid, 'output_type': 'EGGS'})
    
    with pytest.raises(PermissionError) as exc:
        books.append(d.store, owner, sale_restricted)
    assert 'Active Treatment Hold: Eggs/meat from Flock 3 cannot be sold until Jan 06' in str(exc.value)
    assert 'Safe for internal recording and disposal only' in str(exc.value)
    
    # After clearance date, sale succeeds
    sale_cleared = entry(kind='SALE', observed_at='2026-01-08T10:00:00+01:00',
                         details={'category': 'EGG_SALES', 'contact_id': None, 'due_on': None, 'items': [],
                                  'flock_id': fid, 'output_type': 'EGGS'})
    assert books.append(d.store, owner, sale_cleared)['status'] == 'RECORDED'


def test_worker_cannot_self_issue_or_release_restriction(dashboard):
    d = dashboard; owner = d.credentials['principal']
    fid = flock(d)
    hid = health_event(d, fid)
    svc = IdentityService(d.store)
    svc.create_user(owner, 'pen-worker', PASSWORD, 'Worker', ('farming',))
    _, worker = svc.login('pen-worker', PASSWORD)
    
    with pytest.raises(PermissionError):
        health.append_restriction(d.store, worker, restriction(fid, hid))


def test_http_treatment_restrictions_endpoint(dashboard):
    d = dashboard; owner = d.credentials['principal']
    fid = flock(d)
    hid = health_event(d, fid)
    raw = d.credentials['raw']
    
    # POST endpoint
    res = restriction(fid, hid)
    code, _, body = request(d, '/api/farm/treatment-restrictions', method='POST', body=res, raw=raw)
    assert code == 200 and body['status'] == 'RECORDED'
    
    # GET endpoint
    code, _, body = request(d, f'/api/farm/treatment-restrictions?flock_id={fid}', raw=raw)
    assert code == 200 and body['total'] == 1
