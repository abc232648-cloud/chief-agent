import uuid

import pytest

from domains.farming import health_records, journal, reports, staff as staff_domain, tasks
from operations.time_integrity import utc_now, utc_text
from tests.test_farm_tasks import create_task, staff
from tests.test_identity_http import request


def ident():
    return str(uuid.uuid4())


def report(**changes):
    payload = dict(
        event_id=ident(), category='OBSERVATION', task_id=None, entity_id=None,
        location='House 1', observed_at=utc_text(utc_now()),
        summary='Birds observed active around the drinker line.',
        quantity=None, unit=None, basis=None, severity='ROUTINE',
    )
    payload.update(changes)
    return payload


def test_worker_report_is_human_evidence_not_a_domain_side_effect(dashboard):
    d = dashboard
    _, worker_id, _, _, _, manager, _, worker, other, _, _ = staff(d)
    before_journal = len(journal.read_rows(d.store._connect().__enter__())) if False else None
    with d.store._connect() as con:
        journal_count = len(journal.read_rows(con))
        health_count = len(health_records.rows(con))
        staff_count = len(staff_domain.rows(con))
        task_count = len(tasks.rows(con))

    payload = report(category='MORTALITY', quantity='4', unit='birds', basis='COUNTED', severity='IMPORTANT')
    result = reports.append(d.store, worker, payload)
    assert result['status'] == 'RECEIVED'
    assert result['report_id'] == payload['event_id']
    assert result['evidence_status'] == 'HUMAN_REPORTED'
    assert reports.append(d.store, worker, payload)['status'] == 'ALREADY_RECEIVED'

    with d.store._connect() as con:
        assert len(journal.read_rows(con)) == journal_count
        assert len(health_records.rows(con)) == health_count
        assert len(staff_domain.rows(con)) == staff_count
        assert len(tasks.rows(con)) == task_count

    assert reports.overview(d.store, worker)['total'] == 1
    assert reports.overview(d.store, manager)['total'] == 1
    assert reports.overview(d.store, other)['total'] == 0


def test_report_event_id_conflict_and_unsupported_incident_emergency_fail_closed(dashboard):
    d = dashboard
    _, _, _, _, _, _, _, worker, _, _, _ = staff(d)
    payload = report()
    reports.append(d.store, worker, payload)
    with pytest.raises(ValueError, match='Submission identifier conflict'):
        reports.append(d.store, worker, {**payload, 'summary': 'Changed after id reuse.'})
    with pytest.raises(ValueError, match='supported worker-report category'):
        reports.append(d.store, worker, report(category='INCIDENT'))
    with pytest.raises(ValueError, match='supported worker-report category'):
        reports.append(d.store, worker, report(category='EMERGENCY'))


def test_task_evidence_requires_visible_authoritative_task(dashboard):
    d = dashboard
    _, worker_id, other_id, supervisor_id, _, manager, _, worker, other, _, supervisor = staff(d)
    creation = create_task(worker_id, supervisor_id)
    tasks.append(d.store, manager, creation)

    linked = report(category='TASK_EVIDENCE', task_id=creation['task_id'], summary='Completed the requested field check.')
    assert reports.append(d.store, worker, linked)['status'] == 'RECEIVED'
    supervisor_view = reports.overview(d.store, supervisor)
    assert supervisor_view['total'] == 1
    assert supervisor_view['reports'][0]['payload']['task_id'] == creation['task_id']

    with pytest.raises(PermissionError, match='outside your authenticated task scope'):
        reports.append(d.store, other, report(category='TASK_EVIDENCE', task_id=creation['task_id']))
    with pytest.raises(ValueError, match='must reference'):
        reports.append(d.store, worker, report(category='TASK_EVIDENCE', task_id=None))
    assert reports.overview(d.store, other)['total'] == 0
    assert other_id != worker_id


def test_report_quantity_rules_preserve_unknowns_and_mortality_count_truth(dashboard):
    d = dashboard
    _, _, _, _, _, _, _, worker, _, _, _ = staff(d)
    with pytest.raises(ValueError, match='Unit and basis'):
        reports.append(d.store, worker, report(quantity=None, unit='birds', basis=None))
    with pytest.raises(ValueError, match='whole bird count'):
        reports.append(d.store, worker, report(category='MORTALITY', quantity='1.5', unit='birds', basis='COUNTED'))
    with pytest.raises(ValueError, match='whole bird count'):
        reports.append(d.store, worker, report(category='MORTALITY', quantity='2', unit='kg', basis='COUNTED'))
    result = reports.append(d.store, worker, report(category='FEED', quantity='2.5', unit='kg', basis='ESTIMATED'))
    assert result['status'] == 'RECEIVED'


def test_worker_report_http_endpoint_returns_received_not_verified(dashboard):
    d = dashboard
    _, _, _, _, _, _, worker_raw, _, _, _, _ = staff(d)
    payload = report(category='WATER', severity='IMPORTANT')
    code, _, body = request(d, '/api/farm/worker-reports', 'POST', payload, worker_raw)
    assert code == 200
    assert body['status'] == 'RECEIVED'
    assert body['evidence_status'] == 'HUMAN_REPORTED'
    assert 'verificationHash' not in body
    assert 'verified' not in body
    code, _, body = request(d, '/api/farm/worker-reports', raw=worker_raw)
    assert code == 200 and body['total'] == 1
    assert 'does not update stock balances' in body['notice']
