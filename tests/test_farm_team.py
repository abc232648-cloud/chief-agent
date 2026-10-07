from datetime import timedelta

from domains.farming import staff as staff_domain, tasks, team
from operations.time_integrity import utc_now, utc_text
from tests.test_farm_tasks import create_task, event, staff


def test_supervisor_team_is_derived_only_from_supervised_tasks(dashboard):
    d = dashboard
    _, worker_id, other_id, supervisor_id, _, manager, _, worker, other, _, supervisor = staff(d)

    supervised = create_task(worker_id, supervisor_id)
    unsupervised = create_task(other_id, None)
    tasks.append(d.store, manager, supervised)
    tasks.append(d.store, manager, unsupervised)

    view = team.overview(d.store, supervisor)
    assert view['scope'] == 'SUPERVISED_TASK_ASSIGNMENTS'
    assert view['member_count'] == 1
    assert [member['user_id'] for member in view['members']] == [worker_id]
    assert view['members'][0]['assigned_task_count'] == 1
    assert view['members'][0]['attendance_status'] == 'NOT_CONNECTED'
    assert view['attendance_available'] is False
    assert other_id not in {member['user_id'] for member in view['members']}

    worker_view = team.overview(d.store, worker)
    assert worker_view['scope'] == 'SELF'
    assert [member['user_id'] for member in worker_view['members']] == [worker_id]
    other_view = team.overview(d.store, other)
    assert [member['user_id'] for member in other_view['members']] == [other_id]


def test_manager_sees_enabled_farm_staff_directory_without_privileged_identities(dashboard):
    d = dashboard
    _, worker_id, other_id, supervisor_id, _, manager, _, _, _, _, _ = staff(d)
    view = team.overview(d.store, manager)
    ids = {member['user_id'] for member in view['members']}
    assert view['scope'] == 'FARM_DIRECTORY'
    assert {worker_id, other_id, supervisor_id}.issubset(ids)
    assert all(member['enabled'] is True for member in view['members'])
    assert all(member['chief_role'] not in {'Owner', 'Administrator'} for member in view['members'])
    assert view['attendance_available'] is False


def test_verification_queue_is_scope_limited(dashboard):
    d = dashboard
    _, worker_id, other_id, supervisor_id, _, manager, _, worker, other, _, supervisor = staff(d)
    supervised = create_task(worker_id, supervisor_id)
    unrelated = create_task(other_id, None)
    tasks.append(d.store, manager, supervised)
    tasks.append(d.store, manager, unrelated)

    ack = event('ACKNOWLEDGE', supervised['task_id'], supervised['event_id']); tasks.append(d.store, worker, ack)
    submitted = event('SUBMIT', supervised['task_id'], ack['event_id'], note='Ready for field verification.'); tasks.append(d.store, worker, submitted)
    ack2 = event('ACKNOWLEDGE', unrelated['task_id'], unrelated['event_id']); tasks.append(d.store, other, ack2)
    submitted2 = event('SUBMIT', unrelated['task_id'], ack2['event_id'], note='Unrelated verification.'); tasks.append(d.store, other, submitted2)

    supervisor_view = team.overview(d.store, supervisor)
    assert supervisor_view['verification_count'] == 1
    assert supervisor_view['verification_task_ids'] == [supervised['task_id']]
    manager_view = team.overview(d.store, manager)
    assert set(manager_view['verification_task_ids']) == {supervised['task_id'], unrelated['task_id']}


def test_staff_overview_no_longer_exposes_farmwide_legacy_roster_to_supervisor(dashboard):
    d = dashboard
    _, worker_id, other_id, supervisor_id, _, manager, _, worker, _, _, supervisor = staff(d)
    supervised = create_task(worker_id, supervisor_id)
    tasks.append(d.store, manager, supervised)

    # Manager-created legacy work for another worker must not become Supervisor-visible
    # merely because the caller has a Supervisor Farm role.
    legacy = {
        'event_id': 'legacytask001', 'kind': 'TASK', 'reference': None, 'expected_event': None,
        'assignee': other_id, 'due_at': utc_text(utc_now() + timedelta(days=1)),
        'text': 'Legacy task outside this supervisor relationship.', 'severity': 'ROUTINE'
    }
    staff_domain.append(d.store, manager, legacy)

    supervisor_view = staff_domain.overview(d.store, supervisor)
    assert supervisor_view['items'] == []
    assert supervisor_view['assignees'] == []
    assert [member['user_id'] for member in supervisor_view['team']['members']] == [worker_id]
    assert supervisor_view['team']['attendance_available'] is False
