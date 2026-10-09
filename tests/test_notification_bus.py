from dataclasses import FrozenInstanceError

import pytest

from application.composition import default_interface_registry
from database.store import Store
from notifications.bus import NotificationBus, NotificationContext, EVENT_RECORD_KIND
from notifications.events import NotificationAudience, NotificationEvent, NotificationLink
from notifications.models import NotificationSeverity
from notifications.web_push import dispatch_pending, register_subscription


ENV = {
    'CHIEF_INSTANCE_MODE': 'preview',
    'WEB_PUSH_VAPID_PUBLIC_KEY': 'C' * 87,
    'WEB_PUSH_VAPID_PRIVATE_KEY': 'development-private-key',
    'WEB_PUSH_VAPID_SUBJECT': 'mailto:owner@example.invalid',
}
SUBSCRIPTION = {
    'endpoint': 'https://web.push.apple.com/Q-shared-bus-test',
    'expirationTime': None,
    'keys': {'p256dh': 'A' * 87, 'auth': 'B' * 22},
}


def bus(tmp_path):
    state = Store(tmp_path / 'worker.db')
    return state, NotificationBus(state, default_interface_registry())


def job_event(event_id='job-event-1', **changes):
    values = dict(
        event_id=event_id,
        source_agent='jobs',
        event_type='policy.ask',
        title='Application review required',
        body='Review the prepared application before Chief continues.',
        severity=NotificationSeverity.ACTION_REQUIRED,
        audience=NotificationAudience(('owner', 'companion')),
        links=(
            NotificationLink('owner', 'jobs', 'actions'),
            NotificationLink('companion', 'jobs', 'actions'),
        ),
        object_type='application',
        object_id='application-17',
        priority=80,
        created_at='2026-10-09T01:00:00+00:00',
    )
    values.update(changes)
    return NotificationEvent(**values)


def farm_staff_event(event_id='farm-event-1', **changes):
    values = dict(
        event_id=event_id,
        source_agent='farming',
        event_type='task.assigned',
        title='Farm task assigned',
        body='A field task requires your attention.',
        severity='ACTION_REQUIRED',
        audience=NotificationAudience(('staff',), ('Worker',), ('worker-1',)),
        links=(NotificationLink('staff', 'farming', 'tasks/task-22'),),
        object_type='task',
        object_id='task-22',
        priority=60,
        created_at='2026-10-09T01:05:00+00:00',
    )
    values.update(changes)
    return NotificationEvent(**values)


def test_event_contract_is_bounded_structured_and_immutable():
    event = job_event()
    assert event.as_dict()['schema_version'] == 1
    assert event.link_for('owner').path == '/owner/jobs/actions'
    assert event.link_for('companion').path == '/jobs/actions'
    assert set(event.as_dict()) == {
        'schema_version', 'event_id', 'source_agent', 'event_type', 'title', 'body', 'severity',
        'audience', 'links', 'object_type', 'object_id', 'priority', 'created_at',
    }
    with pytest.raises(FrozenInstanceError):
        event.priority = 1
    with pytest.raises(ValueError):
        NotificationEvent.from_dict({**event.as_dict(), 'raw_payload': {'secret': 'no'}})


@pytest.mark.parametrize('route', [
    '/outside', '../secret', 'task/../secret', 'https://evil.example/x', 'task?secret=x', 'task#fragment',
])
def test_deep_links_reject_external_or_escaping_routes(route):
    with pytest.raises(ValueError):
        NotificationLink('staff', 'farming', route)


def test_staff_assignment_scopes_require_explicit_recipients():
    # Manager is domain-scoped, so a domain-wide Staff event is representable.
    manager = NotificationAudience(('staff',), ('Manager',))
    assert manager.principal_ids == ()

    for role in ('Supervisor', 'Worker'):
        with pytest.raises(ValueError, match='explicit recipient'):
            NotificationAudience(('staff',), (role,))

    with pytest.raises(ValueError):
        NotificationAudience(('owner',), ('Manager',))


def test_bus_rejects_surfaces_roles_and_modules_not_declared_by_agent(tmp_path):
    _, shared = bus(tmp_path)
    with pytest.raises(ValueError, match='does not expose'):
        shared.publish(job_event(audience=NotificationAudience(('staff',), ('Manager',))))

    farm_companion = NotificationEvent(
        event_id='farm-companion', source_agent='farming', event_type='incident.alert',
        title='Incident', body='Review incident.', severity='URGENT',
        audience=NotificationAudience(('companion',)),
    )
    with pytest.raises(ValueError, match='does not expose'):
        shared.publish(farm_companion)

    wrong_module = job_event(links=(NotificationLink('owner', 'farming', 'actions'), NotificationLink('companion', 'jobs', 'actions')))
    with pytest.raises(ValueError, match='module'):
        shared.publish(wrong_module)


def test_publish_is_atomic_idempotent_and_keeps_legacy_projection(tmp_path):
    state, shared = bus(tmp_path)
    event = job_event()
    first = shared.publish(event)
    replay = shared.publish(event)
    assert first['status'] == 'RECORDED'
    assert replay['status'] == 'ALREADY_RECORDED'
    assert replay['record_id'] == first['record_id']
    assert replay['legacy_notification_id'] == first['legacy_notification_id']

    with state._connect() as con:
        assert con.execute('SELECT count(*) FROM domain_records WHERE kind=?', (EVENT_RECORD_KIND,)).fetchone()[0] == 1
        row = con.execute('SELECT title,body,severity,domain,related_page FROM notifications').fetchone()
        assert dict(row) == {
            'title': event.title,
            'body': event.body,
            'severity': 'ACTION_REQUIRED',
            'domain': 'jobs',
            'related_page': 'actions',
        }

    with pytest.raises(ValueError, match='identifier conflict'):
        shared.publish(job_event(title='Conflicting reuse of event id'))
    with state._connect() as con:
        assert con.execute('SELECT count(*) FROM notifications').fetchone()[0] == 1
        assert con.execute('SELECT count(*) FROM domain_records WHERE kind=?', (EVENT_RECORD_KIND,)).fetchone()[0] == 1


def test_staff_only_event_is_durable_without_leaking_into_legacy_owner_inbox(tmp_path):
    state, shared = bus(tmp_path)
    receipt = shared.publish(farm_staff_event())
    assert receipt['status'] == 'RECORDED'
    assert receipt['legacy_notification_id'] is None
    with state._connect() as con:
        assert con.execute('SELECT count(*) FROM notifications').fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM domain_records WHERE domain='farming' AND kind=?", (EVENT_RECORD_KIND,)).fetchone()[0] == 1


def test_staff_routing_is_domain_role_and_identity_scoped(tmp_path):
    _, shared = bus(tmp_path)
    worker_event = farm_staff_event()
    shared.publish(worker_event)

    worker = NotificationContext('staff', 'worker-1', 'Worker', ('farming',), 'Worker')
    other_worker = NotificationContext('staff', 'worker-2', 'Worker', ('farming',), 'Worker')
    wrong_domain = NotificationContext('staff', 'worker-1', 'Worker', ('jobs',), 'Worker')
    manager = NotificationContext('staff', 'manager-1', 'Manager', ('farming',), 'Manager')

    projected = shared.project(worker_event, worker)
    assert projected['event_id'] == worker_event.event_id
    assert projected['deep_link']['path'] == '/staff/farming/tasks/task-22'
    assert projected['authority'] == 'PRESENTATION_ONLY'
    assert shared.project(worker_event, other_worker) is None
    assert shared.project(worker_event, wrong_domain) is None
    assert shared.project(worker_event, manager) is None

    manager_event = farm_staff_event(
        event_id='manager-domain-event',
        event_type='farm.notice',
        audience=NotificationAudience(('staff',), ('Manager',)),
        links=(NotificationLink('staff', 'farming', 'overview'),),
        object_type=None,
        object_id=None,
    )
    shared.publish(manager_event)
    assert shared.project(manager_event, manager) is not None
    assert shared.project(manager_event, worker) is None


def test_supervisor_delivery_never_broadcasts_to_peer_supervisor(tmp_path):
    _, shared = bus(tmp_path)
    event = farm_staff_event(
        event_id='supervisor-verify',
        event_type='task.verify',
        audience=NotificationAudience(('staff',), ('Supervisor',), ('supervisor-1',)),
        links=(NotificationLink('staff', 'farming', 'tasks/task-22'),),
    )
    shared.publish(event)
    assigned = NotificationContext('staff', 'supervisor-1', 'Worker', ('farming',), 'Supervisor')
    peer = NotificationContext('staff', 'supervisor-2', 'Worker', ('farming',), 'Supervisor')
    assert shared.project(event, assigned) is not None
    assert shared.project(event, peer) is None


def test_owner_and_companion_routing_respects_manifest_roles_and_domain_scope(tmp_path):
    _, shared = bus(tmp_path)
    event = job_event()
    shared.publish(event)
    owner = NotificationContext('owner', 'owner-1', 'Owner', ('jobs',))
    admin = NotificationContext('companion', 'admin-1', 'Administrator', ('*',))
    worker = NotificationContext('companion', 'worker-1', 'Worker', ('jobs',))
    farm_owner = NotificationContext('owner', 'owner-2', 'Owner', ('farming',))
    assert shared.project(event, owner)['deep_link']['path'] == '/owner/jobs/actions'
    assert shared.project(event, admin)['deep_link']['path'] == '/jobs/actions'
    assert shared.project(event, worker) is None
    assert shared.project(event, farm_owner) is None


def test_list_for_returns_only_visible_valid_events(tmp_path):
    state, shared = bus(tmp_path)
    shared.publish(farm_staff_event())
    shared.publish(farm_staff_event(
        event_id='worker-2-event',
        audience=NotificationAudience(('staff',), ('Worker',), ('worker-2',)),
    ))
    with state._connect() as con:
        con.execute(
            "INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",
            (EVENT_RECORD_KIND, '{not-json', 1.0),
        )
    worker = NotificationContext('staff', 'worker-1', 'Worker', ('farming',), 'Worker')
    result = shared.list_for(worker)
    assert [item['event_id'] for item in result] == ['farm-event-1']
    assert all(item['authority'] == 'PRESENTATION_ONLY' for item in result)


def test_shared_bus_feeds_existing_job_web_push_without_changing_push_contract(tmp_path):
    state, shared = bus(tmp_path)
    register_subscription(state, 'owner-1', SUBSCRIPTION, ENV)
    shared.publish(job_event(event_id='push-compatible-event'))

    delivered = []
    result = dispatch_pending(
        state,
        sender=lambda subscription, payload, settings: delivered.append(payload),
        env=ENV,
    )
    assert result == {'status': 'DELIVERED', 'sent': 1, 'failed': 0, 'expired': 0}
    assert delivered == [{
        'title': 'Application review required',
        'body': 'Review the prepared application before Chief continues.',
        'severity': 'ACTION_REQUIRED',
        'tag': f"chief-job-{state.notifications(1)[0]['id']}",
        'url': '/jobs/',
        'related_page': 'actions',
    }]
