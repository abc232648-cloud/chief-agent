from datetime import timedelta
import uuid

import pytest

from domains.farming import tasks
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_farm_setup import assign
from tests.test_identity_http import request


def ident():
    return str(uuid.uuid4())


def create_task(worker_id, supervisor_id=None, **changes):
    payload = dict(
        event_id=ident(), operation='CREATE', task_id=ident(), title='Check poultry water line',
        instructions='Walk the assigned line and report any leak or loss of pressure.',
        location='House 1', entity_id=None, assigned_to=worker_id, supervisor_id=supervisor_id,
        due_at=utc_text(utc_now() + timedelta(days=1)), priority='ROUTINE',
        evidence_required=['NOTE'], sop_ref=None,
    )
    payload.update(changes)
    return payload


def event(operation, task, revision, **changes):
    payload = dict(event_id=ident(), operation=operation, task_id=task, expected_revision=revision)
    payload.update(changes)
    return payload


def staff(dashboard):
    d = dashboard; service = IdentityService(d.store); owner = d.credentials['principal']
    manager_id = service.create_user(owner, 'task-manager', PASSWORD, 'Manager', ('farming',))
    worker_id = service.create_user(owner, 'task-worker', PASSWORD, 'Worker', ('farming',))
    other_id = service.create_user(owner, 'task-other', PASSWORD, 'Worker', ('farming',))
    supervisor_id = service.create_user(owner, 'task-supervisor', PASSWORD, 'Worker', ('farming',))
    assign(d.store, owner, supervisor_id, 'SUPERVISOR')
    manager_raw, manager = service.login('task-manager', PASSWORD)
    worker_raw, worker = service.login('task-worker', PASSWORD)
    _, other = service.login('task-other', PASSWORD)
    supervisor_raw, supervisor = service.login('task-supervisor', PASSWORD)
    return manager_id, worker_id, other_id, supervisor_id, manager_raw, manager, worker_raw, worker, other, supervisor_raw, supervisor


def test_task_visibility_is_assignment_scoped_and_manager_created(dashboard):
    d = dashboard
    _, worker_id, _, supervisor_id, _, manager, _, worker, other, _, supervisor = staff(d)
    creation = create_task(worker_id, supervisor_id)
    assert tasks.append(d.store, manager, creation)['status'] == 'RECORDED'
    assert tasks.append(d.store, manager, creation)['status'] == 'ALREADY_RECORDED'
    assert tasks.overview(d.store, worker)['total'] == 1
    assert tasks.overview(d.store, supervisor)['total'] == 1
    assert tasks.overview(d.store, manager)['total'] == 1
    assert tasks.overview(d.store, other)['total'] == 0
    item = tasks.overview(d.store, worker)['tasks'][0]
    assert item['status'] == 'ASSIGNED'
    assert item['revision'] == creation['event_id']
    assert item['capabilities']['can_acknowledge'] is True
    assert item['capabilities']['can_verify'] is False
    with pytest.raises(PermissionError):
        tasks.append(d.store, other, event('ACKNOWLEDGE', creation['task_id'], creation['event_id']))


def test_task_revision_and_role_gates_fail_closed(dashboard):
    d = dashboard
    _, worker_id, _, supervisor_id, _, manager, _, worker, _, _, supervisor = staff(d)
    creation = create_task(worker_id, supervisor_id)
    tasks.append(d.store, manager, creation)
    ack = event('ACKNOWLEDGE', creation['task_id'], creation['event_id'])
    assert tasks.append(d.store, worker, ack)['revision'] == ack['event_id']
    with pytest.raises(ValueError, match='changed elsewhere'):
        tasks.append(d.store, worker, event('START', creation['task_id'], creation['event_id']))
    start = event('START', creation['task_id'], ack['event_id'])
    tasks.append(d.store, worker, start)
    submit = event('SUBMIT', creation['task_id'], start['event_id'], note='Water line inspected; no leak observed.')
    tasks.append(d.store, worker, submit)
    with pytest.raises(PermissionError):
        tasks.append(d.store, worker, event('VERIFY', creation['task_id'], submit['event_id'], note='self verify'))
    verify = event('VERIFY', creation['task_id'], submit['event_id'], note='Field completion checked.')
    tasks.append(d.store, supervisor, verify)
    final = tasks.overview(d.store, worker)['tasks'][0]
    assert final['status'] == 'VERIFIED'
    assert final['revision'] == verify['event_id']
    assert all(not final['capabilities'][key] for key in ('can_acknowledge','can_start','can_submit','can_verify','can_reassign','can_cancel'))


def test_supervisor_cannot_see_or_verify_unassigned_team_task(dashboard):
    d = dashboard
    _, worker_id, _, supervisor_id, _, manager, _, worker, _, _, supervisor = staff(d)
    creation = create_task(worker_id, None)
    tasks.append(d.store, manager, creation)
    assert tasks.overview(d.store, supervisor)['tasks'] == []
    ack = event('ACKNOWLEDGE', creation['task_id'], creation['event_id']); tasks.append(d.store, worker, ack)
    submit = event('SUBMIT', creation['task_id'], ack['event_id'], note='Completed.'); tasks.append(d.store, worker, submit)
    with pytest.raises(PermissionError):
        tasks.append(d.store, supervisor, event('VERIFY', creation['task_id'], submit['event_id'], note='not scoped'))
    assert tasks.overview(d.store, manager)['tasks'][0]['capabilities']['can_verify'] is True


def test_task_http_endpoint_and_client_local_state_not_accepted(dashboard):
    d = dashboard
    _, worker_id, _, supervisor_id, manager_raw, manager, worker_raw, _, _, _, _ = staff(d)
    creation = create_task(worker_id, supervisor_id)
    code, _, body = request(d, '/api/farm/tasks', 'POST', creation, manager_raw)
    assert code == 200 and body['status'] == 'RECORDED'
    code, _, body = request(d, '/api/farm/tasks', raw=worker_raw)
    assert code == 200 and body['total'] == 1 and body['tasks'][0]['status'] == 'ASSIGNED'
    invalid = event('COMPLETED_LOCALLY', creation['task_id'], creation['event_id'])
    code, _, _ = request(d, '/api/farm/tasks', 'POST', invalid, worker_raw)
    assert code == 400
    assert tasks.overview(d.store, manager)['tasks'][0]['status'] == 'ASSIGNED'


def test_task_create_rejects_invalid_assignee_and_event_id_conflict(dashboard):
    d = dashboard
    _, worker_id, _, _, _, manager, _, _, _, _, _ = staff(d)
    with pytest.raises(PermissionError):
        tasks.append(d.store, manager, create_task(ident()))
    creation = create_task(worker_id)
    tasks.append(d.store, manager, creation)
    with pytest.raises(ValueError, match='Submission identifier conflict'):
        tasks.append(d.store, manager, {**creation, 'title': 'Changed after event id reuse'})
