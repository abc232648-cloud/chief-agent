from datetime import timedelta
import uuid

import pytest

from domains.farming import incidents
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_setup import assign
from tests.test_identity_http import request


def ident():
    return str(uuid.uuid4())


def incident(**changes):
    payload = dict(
        event_id=ident(), operation='CREATE', kind='INCIDENT', category='WATER',
        location='House 1', observed_at=utc_text(utc_now() - timedelta(minutes=1)),
        summary='Water pressure dropped below the expected level.', severity='IMPORTANT',
        immediate_risk=False,
    )
    payload.update(changes)
    return payload


def staff(dashboard):
    d = dashboard; service = IdentityService(d.store); owner = d.credentials['principal']
    manager_id = service.create_user(owner, 'incident-manager', PASSWORD, 'Manager', ('farming',))
    worker_id = service.create_user(owner, 'incident-worker', PASSWORD, 'Worker', ('farming',))
    other_id = service.create_user(owner, 'incident-other', PASSWORD, 'Worker', ('farming',))
    supervisor_id = service.create_user(owner, 'incident-supervisor', PASSWORD, 'Worker', ('farming',))
    assign(d.store, owner, supervisor_id, 'SUPERVISOR')
    manager_raw, manager = service.login('incident-manager', PASSWORD)
    worker_raw, worker = service.login('incident-worker', PASSWORD)
    _, other = service.login('incident-other', PASSWORD)
    supervisor_raw, supervisor = service.login('incident-supervisor', PASSWORD)
    return manager_id, worker_id, other_id, supervisor_id, manager_raw, manager, worker_raw, worker, other, supervisor_raw, supervisor


def escalate(item, **changes):
    payload = dict(
        event_id=ident(), operation='ESCALATE', incident_id=item['incident_id'],
        expected_revision=item['revision'], reason='Supervisor/Owner attention is required.'
    )
    payload.update(changes)
    return payload


def test_incident_visibility_receipt_and_idempotency(dashboard):
    d = dashboard
    _, _, _, _, _, manager, _, worker, other, _, supervisor = staff(d)
    payload = incident()
    first = incidents.append(d.store, worker, payload)
    assert first['status'] == 'RECORDED'
    assert first['incident_id'] == payload['event_id']
    assert incidents.append(d.store, worker, payload)['status'] == 'ALREADY_RECORDED'
    assert incidents.overview(d.store, worker)['total'] == 1
    assert incidents.overview(d.store, other)['total'] == 0
    assert incidents.overview(d.store, supervisor)['total'] == 1
    assert incidents.overview(d.store, manager)['total'] == 1
    record = incidents.overview(d.store, worker)['incidents'][0]
    assert record['revision'] == payload['event_id']
    assert record['status'] == 'OPEN'
    assert record['can_escalate'] is False
    assert incidents.overview(d.store, supervisor)['incidents'][0]['can_escalate'] is True
    with pytest.raises(ValueError, match='Submission identifier conflict'):
        incidents.append(d.store, worker, {**payload, 'summary': 'Altered after event-id reuse'})


def test_incident_escalation_is_scoped_and_revision_guarded(dashboard):
    d = dashboard
    _, _, _, _, _, _, _, worker, other, _, supervisor = staff(d)
    incidents.append(d.store, worker, incident())
    current = incidents.overview(d.store, worker)['incidents'][0]
    with pytest.raises(PermissionError):
        incidents.append(d.store, worker, escalate(current))
    with pytest.raises(PermissionError):
        incidents.append(d.store, other, escalate(current))
    stale = escalate(current, expected_revision=ident())
    with pytest.raises(ValueError, match='changed elsewhere'):
        incidents.append(d.store, supervisor, stale)
    command = escalate(current)
    result = incidents.append(d.store, supervisor, command)
    assert result['status'] == 'RECORDED'
    updated = incidents.overview(d.store, worker)['incidents'][0]
    assert updated['status'] == 'ESCALATED'
    assert updated['revision'] == command['event_id']
    assert updated['can_escalate'] is False


def test_incident_schema_and_time_fail_closed(dashboard):
    d = dashboard
    _, _, _, _, _, _, _, worker, _, _, _ = staff(d)
    with pytest.raises(ValueError, match='future'):
        incidents.append(d.store, worker, incident(observed_at=utc_text(utc_now() + timedelta(minutes=5))))
    with pytest.raises(ValueError, match='severity'):
        incidents.append(d.store, worker, incident(severity='LOW'))
    with pytest.raises(ValueError, match='boolean'):
        incidents.append(d.store, worker, incident(immediate_risk='yes'))
    malformed = incident(); malformed['fake_server_receipt'] = True
    with pytest.raises(ValueError, match='exactly'):
        incidents.append(d.store, worker, malformed)


def test_incident_http_contract_matches_staff_pwa(dashboard):
    d = dashboard
    _, _, _, _, _, _, worker_raw, _, _, supervisor_raw, _ = staff(d)
    payload = incident(kind='EMERGENCY', severity='CRITICAL', immediate_risk=True)
    code, _, receipt = request(d, '/api/farm/incidents', 'POST', payload, worker_raw)
    assert code == 200
    assert receipt['status'] == 'RECORDED'
    assert receipt['incident_id'] == payload['event_id']
    assert receipt['revision'] == payload['event_id']
    assert receipt['received_at']
    code, _, overview = request(d, '/api/farm/incidents', raw=supervisor_raw)
    assert code == 200 and overview['total'] == 1
    item = overview['incidents'][0]
    assert item['kind'] == 'EMERGENCY' and item['severity'] == 'CRITICAL'
    assert item['can_escalate'] is True
