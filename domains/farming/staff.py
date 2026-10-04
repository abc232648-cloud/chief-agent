"""Human staff work. Append-only history; no automatic dispatch or notifications."""
import json
import time
from . import setup
from .journal import bounded_text, identifier
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text, aware_utc

KIND = 'farm_staff_v1'
FIELDS = {'event_id', 'kind', 'reference', 'expected_event', 'assignee', 'due_at', 'text', 'severity'}
STATES = {'TASK': 'OPEN', 'INCIDENT': 'OPEN', 'ACKNOWLEDGE': 'ACKNOWLEDGED',
          'ESCALATE': 'ESCALATED', 'REPORT_COMPLETION': 'COMPLETION_REPORTED',
          'RESOLVE': 'RESOLVED', 'REOPEN': 'OPEN'}


def rows(con):
    return [json.loads(r[0]) for r in con.execute(
        'SELECT data_json FROM domain_records WHERE domain=? AND kind=? ORDER BY id', ('farming', KIND))]


def projection(history):
    result = {}
    for r in history:
        p = r['payload']
        key = p['reference'] or p['event_id']
        if p['reference'] is None:
            result[key] = {'id': key, 'kind': p['kind'], 'reporter': r['actor_id'], 'assignee': p['assignee'],
                           'text': p['text'], 'due_at': p['due_at'], 'severity': p['severity'], 'history': []}
        item = result[key]
        item['state'] = STATES[p['kind']]
        item['latest_event'] = p['event_id']
        item['history'].append(r)
        item['overdue'] = bool(item['due_at'] and aware_utc(item['due_at']) < utc_now() and item['state'] != 'RESOLVED')
    return result


def append(store, principal, payload):
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        raise ValueError('Supply exactly the staff-work fields.')
    p = dict(payload)
    identifier(p['event_id'])
    if not isinstance(p['kind'], str) or p['kind'] not in STATES:
        raise ValueError('Unknown staff-work event.')
    p['text'] = bounded_text(p['text'], 2000)
    if p['severity'] not in ('ROUTINE', 'IMPORTANT', 'URGENT'):
        raise ValueError('Unknown severity.')
    for field in ('reference', 'expected_event', 'assignee'):
        if p[field] is not None:
            identifier(p[field])
    if p['due_at'] is not None:
        if not isinstance(p['due_at'], str) or len(p['due_at']) > 40:
            raise ValueError('A timezone-aware deadline is required.')
        p['due_at'] = utc_text(aware_utc(p['due_at']))
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        action = 'manage' if p['kind'] in {'TASK', 'RESOLVE', 'REOPEN'} else 'report'
        principal = setup.authorize(store, con, principal, action)
        setup.available(store, con)
        history = rows(con)
        prior = next((r for r in history if r['payload']['event_id'] == p['event_id']), None)
        if prior:
            if prior['payload'] != p or prior['actor_id'] != principal.id:
                raise ValueError('Submission identifier conflict.')
            return {'status': 'ALREADY_RECORDED', 'record': prior}
        if p['kind'] in {'TASK', 'INCIDENT'}:
            if p['reference'] is not None or p['expected_event'] is not None:
                raise ValueError('New work does not reference earlier work.')
            if p['kind'] == 'TASK':
                target = con.execute('SELECT domains,enabled FROM human_identities WHERE id=?', (p['assignee'],)).fetchone()
                if not target or not target['enabled'] or not ({'farming', '*'} & set(json.loads(target['domains']))):
                    raise ValueError('Choose an enabled Farm assignee.')
                if p['due_at'] is None:
                    raise ValueError('A task needs an explicit deadline.')
            elif p['assignee'] is not None or p['due_at'] is not None:
                raise ValueError('Incident reporting does not assign work or a deadline.')
        else:
            item = projection(history).get(p['reference'])
            if not item:
                raise ValueError('Work item is unavailable.')
            manager = setup.role(con, principal) in {'OWNER', 'GENERAL_MANAGER', 'SUPERVISOR'}
            if not manager and principal.id not in {item['reporter'], item['assignee']}:
                raise PermissionError('This work item is outside your assignment.')
            if p['expected_event'] != item['latest_event']:
                raise ValueError('Work changed; refresh before reporting a transition.')
            if p['assignee'] is not None or p['due_at'] is not None:
                raise ValueError('Transitions cannot silently change the assignment or deadline.')
            if (item['state'] == 'RESOLVED') != (p['kind'] == 'REOPEN'):
                raise ValueError('Resolved work must be explicitly reopened; open work cannot be reopened.')
        record = {'version': 1, 'payload': p, 'actor_id': principal.id, 'session_id': principal.session_id,
                  'received_at': utc_text(utc_now())}
        con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',
                    ('farming', KIND, json.dumps(record, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_STAFF_' + p['kind'], 'farming', p['event_id'], 'RECORDED')
    return {'status': 'RECORDED', 'record': record}


def overview(store, principal):
    with store._connect() as con:
        principal = setup.authorize(store, con, principal, 'read')
        manager = setup.role(con, principal) in {'OWNER', 'GENERAL_MANAGER', 'SUPERVISOR'}
        items = [item for item in projection(rows(con)).values()
                 if manager or principal.id in {item['reporter'], item['assignee']}]
        roster = []
        if manager:
            roster = [{'id': r['id'], 'username': r['username']} for r in con.execute('SELECT id,username,domains FROM human_identities WHERE enabled=1')
                      if {'farming', '*'} & set(json.loads(r['domains']))]
        from .brief import schedule_status
        return {'items': items, 'can_manage': manager, 'assignees': roster,
                'timezone': 'Africa/Lagos', 'daily_report_schedule': schedule_status(con), 'external_notifications': 'PENDING',
                'open_count': sum(item['state'] != 'RESOLVED' for item in items),
                'overdue_count': sum(item['overdue'] for item in items)}
