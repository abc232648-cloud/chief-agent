"""Cross-surface Farm acceptance chain.

This test models the contract shared by Staff PWA, Chief and Owner PWA. Chief is
always the authority: Staff mutations create/revise Chief state and the Owner
projection observes that same persisted state rather than a client-side copy.
"""

from domains.farming import incidents, reports, tasks
from tests.test_farm_incidents import escalate, incident
from tests.test_farm_tasks import create_task, event, staff
from tests.test_farm_worker_reports import report


def test_manager_worker_supervisor_owner_operational_chain(dashboard):
    d = dashboard
    (
        _manager_id,
        worker_id,
        _other_id,
        supervisor_id,
        _manager_raw,
        manager,
        _worker_raw,
        worker,
        _other,
        _supervisor_raw,
        supervisor,
    ) = staff(d)
    owner = d.credentials['principal']

    # Manager assigns authoritative work.
    creation = create_task(
        worker_id,
        supervisor_id,
        evidence_required=['NOTE'],
        title='Inspect poultry water pressure',
    )
    assert tasks.append(d.store, manager, creation)['status'] == 'RECORDED'
    worker_view = tasks.overview(d.store, worker)['tasks'][0]
    assert worker_view['status'] == 'ASSIGNED'
    assert worker_view['assigned_to'] == worker_id

    # Worker acknowledges, starts, records linked human evidence and submits.
    ack = event('ACKNOWLEDGE', creation['task_id'], worker_view['revision'])
    assert tasks.append(d.store, worker, ack)['revision'] == ack['event_id']
    start = event('START', creation['task_id'], ack['event_id'])
    assert tasks.append(d.store, worker, start)['revision'] == start['event_id']

    evidence = report(
        category='TASK_EVIDENCE',
        task_id=creation['task_id'],
        location='House 1',
        summary='Water line inspected and pressure check completed.',
        severity='ROUTINE',
    )
    evidence_receipt = reports.append(d.store, worker, evidence)
    assert evidence_receipt['status'] == 'RECEIVED'
    assert evidence_receipt['evidence_status'] == 'HUMAN_REPORTED'

    submit = event(
        'SUBMIT',
        creation['task_id'],
        start['event_id'],
        note='Inspection complete; linked field evidence was submitted to Chief.',
    )
    assert tasks.append(d.store, worker, submit)['revision'] == submit['event_id']
    supervisor_task = tasks.overview(d.store, supervisor)['tasks'][0]
    assert supervisor_task['status'] == 'AWAITING_VERIFICATION'
    assert supervisor_task['capabilities']['can_verify'] is True

    # Supervisor verifies; Manager and Owner must observe the exact Chief state.
    verify = event(
        'VERIFY',
        creation['task_id'],
        supervisor_task['revision'],
        note='Field completion and evidence checked.',
    )
    assert tasks.append(d.store, supervisor, verify)['revision'] == verify['event_id']
    manager_task = tasks.overview(d.store, manager)['tasks'][0]
    owner_task = tasks.overview(d.store, owner)['tasks'][0]
    assert manager_task['status'] == owner_task['status'] == 'VERIFIED'
    assert manager_task['revision'] == owner_task['revision'] == verify['event_id']

    owner_reports = reports.overview(d.store, owner)
    assert owner_reports['total'] == 1
    assert owner_reports['reports'][0]['payload']['event_id'] == evidence['event_id']
    assert owner_reports['reports'][0]['evidence_status'] == 'HUMAN_REPORTED'

    # A separate serious field event follows the incident escalation path.
    emergency = incident(
        kind='EMERGENCY',
        category='WATER',
        severity='CRITICAL',
        immediate_risk=True,
        summary='Main poultry water supply failed and requires urgent attention.',
    )
    first_incident_receipt = incidents.append(d.store, worker, emergency)
    assert first_incident_receipt['status'] == 'RECORDED'

    supervisor_incident = incidents.overview(d.store, supervisor)['incidents'][0]
    assert supervisor_incident['incident_id'] == emergency['event_id']
    assert supervisor_incident['can_escalate'] is True
    escalation = escalate(
        supervisor_incident,
        reason='Critical water outage requires Manager and Owner awareness.',
    )
    assert incidents.append(d.store, supervisor, escalation)['status'] == 'RECORDED'

    owner_incident = incidents.overview(d.store, owner)['incidents'][0]
    manager_incident = incidents.overview(d.store, manager)['incidents'][0]
    assert owner_incident['status'] == manager_incident['status'] == 'ESCALATED'
    assert owner_incident['revision'] == manager_incident['revision'] == escalation['event_id']
    assert owner_incident['severity'] == 'CRITICAL'
    assert owner_incident['immediate_risk'] is True


def test_replayed_staff_events_are_idempotent_and_conflicts_fail_closed(dashboard):
    d = dashboard
    _, worker_id, _, supervisor_id, _, manager, _, worker, _, _, supervisor = staff(d)

    creation = create_task(worker_id, supervisor_id)
    first = tasks.append(d.store, manager, creation)
    replay = tasks.append(d.store, manager, creation)
    assert first['status'] == 'RECORDED'
    assert replay['status'] == 'ALREADY_RECORDED'
    assert replay['revision'] == creation['event_id']

    evidence = report(category='TASK_EVIDENCE', task_id=creation['task_id'])
    first_report = reports.append(d.store, worker, evidence)
    replay_report = reports.append(d.store, worker, evidence)
    assert first_report['status'] == 'RECEIVED'
    assert replay_report['status'] == 'ALREADY_RECEIVED'
    assert replay_report['report_id'] == evidence['event_id']

    emergency = incident(kind='EMERGENCY', severity='CRITICAL', immediate_risk=True)
    first_incident = incidents.append(d.store, worker, emergency)
    replay_incident = incidents.append(d.store, worker, emergency)
    assert first_incident['status'] == 'RECORDED'
    assert replay_incident['status'] == 'ALREADY_RECORDED'

    current = incidents.overview(d.store, supervisor)['incidents'][0]
    escalation = escalate(current)
    first_escalation = incidents.append(d.store, supervisor, escalation)
    replay_escalation = incidents.append(d.store, supervisor, escalation)
    assert first_escalation['status'] == 'RECORDED'
    assert replay_escalation['status'] == 'ALREADY_RECORDED'
    assert replay_escalation['revision'] == escalation['event_id']
