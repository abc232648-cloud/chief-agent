"""Domain-owned recipients and reference-only alerts, committed with Farm work."""
import json
import hashlib
from notifications.bus import NotificationBus
from notifications.events import NotificationEvent, NotificationAudience
from . import tasks, incidents


def publish(store, con, record, object_type, object_id):
    exposure = getattr(store, '_module_exposure', None)
    if exposure is None: raise PermissionError('Notification composition unavailable.')
    module = tasks if object_type == 'farm-task' else incidents
    item = module._snapshots(module.rows(con))[object_id]
    recipients = []
    for row in con.execute('SELECT id,role,domains FROM human_identities WHERE enabled=1'):
        if row['role'] not in {'Manager', 'Worker'} or not {'farming', '*'} & set(json.loads(row['domains'])): continue
        role = tasks._farm_role_for_human(con, row['id'], row['role'])
        if module._can_view(role, row['id'], item): recipients.append(row['id'])
    bus = NotificationBus(store, exposure.interfaces)
    base = dict(source_agent='farming', event_type=object_type + '.changed',
                title='Farm task updated' if object_type == 'farm-task' else 'Farm incident updated',
                body='Open Farm records to review the current details.', severity='ACTION_REQUIRED',
                object_type=object_type, object_id=object_id, created_at=record['received_at'])
    bus.publish(NotificationEvent(**base, event_id=hashlib.sha256((record['payload']['event_id'] + '-owner').encode()).hexdigest(),
                audience=NotificationAudience(('owner',))), connection=con)
    if recipients:
        bus.publish(NotificationEvent(**base, event_id=hashlib.sha256((record['payload']['event_id'] + '-staff').encode()).hexdigest(),
                    audience=NotificationAudience(('staff',), ('Manager', 'Supervisor', 'Worker'), tuple(sorted(recipients)))), connection=con)
