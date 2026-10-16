"""Review-only probes: demonstrate gaps, not production acceptance tests."""
import pytest
from domains.farming import bookkeeping as books, health_records as health
from tests.test_farm_bookkeeping import entry
from tests.test_farm_treatment_restrictions import flock, health_event, restriction


@pytest.mark.parametrize('variant', ['missing_details', 'missing_flock', 'unknown_flock', 'invalid_output'])
def test_treatment_sale_bypass_is_reproducible(dashboard, variant):
    d=dashboard;owner=d.credentials['principal'];fid=flock(d);hid=health_event(d,fid)
    health.append_restriction(d.store,owner,restriction(fid,hid,active_until=None))
    details=dict(category='EGG_SALES',contact_id=None,due_on=None,items=[],flock_id=fid,output_type='EGGS')
    sale=entry(observed_at='2026-01-03T10:00:00+01:00',details=details)
    with pytest.raises(PermissionError):books.append(d.store,owner,sale)
    if variant=='missing_details':sale.pop('details')
    elif variant=='missing_flock':details.pop('flock_id')
    elif variant=='unknown_flock':details['flock_id']='unregistered-flock'
    else:details['output_type']='OTHER'
    assert books.append(d.store,owner,sale)['status']=='RECORDED', 'Candidate no longer reproduces bypass; update finding.'


def test_medicine_without_separate_restriction_has_no_automatic_hold(dashboard):
    d=dashboard;owner=d.credentials['principal'];fid=flock(d);health_event(d,fid)
    with d.store._connect() as con:
        assert health.check_restriction(con,fid,at_time='2026-01-03T10:00:00+01:00')=={'restricted':False}
