"""Authoritative Farm field-task ledger.

Tasks are append-only Chief records. Client-only states such as completed_locally
never enter this ledger. Every mutation is idempotent by event_id and uses
expected_revision so stale clients cannot silently overwrite newer state.
"""
import json
import time

from identity.service import IdentityService
from operations.time_integrity import aware_utc, utc_now, utc_text
from . import setup
from .journal import identifier, bounded_text

KIND = 'farm_task_v1'
LIMIT = 10000
PRIORITIES = {'ROUTINE', 'HIGH', 'URGENT'}
EVIDENCE = {'NONE', 'NOTE', 'PHOTO', 'MEASUREMENT', 'CHECKLIST'}
ACTIVE_STATES = {'ASSIGNED', 'ACKNOWLEDGED', 'IN_PROGRESS', 'AWAITING_VERIFICATION', 'NEEDS_CORRECTION', 'ESCALATED'}
TERMINAL_STATES = {'VERIFIED', 'CANCELLED'}


def rows(con):
    result = con.execute(
        "SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id LIMIT ?",
        (KIND, LIMIT + 1),
    ).fetchall()
    if len(result) > LIMIT:
        raise ValueError('Task history capacity requires indexed upgrade; no truncated task state returned.')
    return [json.loads(row[0]) for row in result]


def _farm_role_for_human(con, human_id, chief_role=None):
    assignments = [r for r in setup.rows(con) if r['payload']['operation'] == 'assign_role' and r['payload']['human_id'] == human_id]
    if assignments:
        return assignments[-1]['payload']['role']
    return 'GENERAL_MANAGER' if chief_role == 'Manager' else 'WORKER'


def _target(con, human_id):
    identifier(human_id)
    row = con.execute('SELECT id,role,domains,enabled FROM human_identities WHERE id=?', (human_id,)).fetchone()
    if not row or not row['enabled'] or not ({'farming', '*'} & set(json.loads(row['domains']))):
        raise PermissionError('Task assignee must be an enabled Farm-scoped identity.')
    if row['role'] in {'Owner', 'Administrator'}:
        raise PermissionError('Use Staff identities for field-task assignment.')
    return row, _farm_role_for_human(con, human_id, row['role'])


def _validate_create(con, p):
    fields = {'event_id', 'operation', 'task_id', 'title', 'instructions', 'location', 'entity_id',
              'assigned_to', 'supervisor_id', 'due_at', 'priority', 'evidence_required', 'sop_ref'}
    if set(p) != fields:
        raise ValueError('Supply exactly the task creation fields.')
    identifier(p['event_id']); identifier(p['task_id']); identifier(p['assigned_to'])
    p['title'] = bounded_text(p['title'], 160)
    p['instructions'] = bounded_text(p['instructions'], 2000)
    p['location'] = bounded_text(p['location'], 120, empty=True)
    if p['entity_id'] is not None:
        identifier(p['entity_id'])
        if p['entity_id'] not in setup.entities(con):
            raise ValueError('Task location entity does not exist. Refresh Farm setup.')
    assignee, _ = _target(con, p['assigned_to'])
    if p['supervisor_id'] is not None:
        identifier(p['supervisor_id'])
        _, supervisor_role = _target(con, p['supervisor_id'])
        if supervisor_role != 'SUPERVISOR':
            raise ValueError('Task supervisor must have the Farm Supervisor assignment.')
        if p['supervisor_id'] == p['assigned_to']:
            raise ValueError('Task supervisor and assignee must be different identities.')
    if not isinstance(p['due_at'], str) or len(p['due_at']) > 40:
        raise ValueError('Task due time including timezone is required.')
    due = aware_utc(p['due_at'])
    if due <= utc_now():
        raise ValueError('Task due time must be in the future.')
    p['due_at'] = utc_text(due)
    if p['priority'] not in PRIORITIES:
        raise ValueError('Choose a known task priority.')
    if not isinstance(p['evidence_required'], list) or len(p['evidence_required']) > 5 or any(v not in EVIDENCE for v in p['evidence_required']):
        raise ValueError('Choose known task evidence requirements.')
    if 'NONE' in p['evidence_required'] and len(p['evidence_required']) != 1:
        raise ValueError('NONE cannot be combined with another evidence requirement.')
    if len(set(p['evidence_required'])) != len(p['evidence_required']):
        raise ValueError('Duplicate evidence requirements are not accepted.')
    if p['sop_ref'] is not None:
        p['sop_ref'] = bounded_text(p['sop_ref'], 160)
    return assignee


def _snapshots(history):
    tasks = {}
    for record in history:
        p = record['payload']; op = p['operation']; task_id = p['task_id']
        if op == 'CREATE':
            tasks[task_id] = {
                'task_id': task_id, 'title': p['title'], 'instructions': p['instructions'],
                'location': p['location'], 'entity_id': p['entity_id'], 'assigned_to': p['assigned_to'],
                'supervisor_id': p['supervisor_id'], 'due_at': p['due_at'], 'priority': p['priority'],
                'evidence_required': list(p['evidence_required']), 'sop_ref': p['sop_ref'],
                'status': 'ASSIGNED', 'revision': p['event_id'], 'created_at': record['received_at'],
                'updated_at': record['received_at'], 'last_actor_id': record['actor_id'],
                'submission_note': None, 'verification_note': None, 'correction_note': None,
                'escalation_reason': None,
            }
            continue
        task = tasks.get(task_id)
        if not task:
            continue
        task['revision'] = p['event_id']; task['updated_at'] = record['received_at']; task['last_actor_id'] = record['actor_id']
        if op == 'ACKNOWLEDGE': task['status'] = 'ACKNOWLEDGED'
        elif op == 'START': task['status'] = 'IN_PROGRESS'
        elif op == 'SUBMIT': task.update(status='AWAITING_VERIFICATION', submission_note=p['note'])
        elif op == 'VERIFY': task.update(status='VERIFIED', verification_note=p['note'])
        elif op == 'REQUEST_CORRECTION': task.update(status='NEEDS_CORRECTION', correction_note=p['note'])
        elif op == 'ESCALATE': task.update(status='ESCALATED', escalation_reason=p['reason'])
        elif op == 'REASSIGN':
            task.update(status='ASSIGNED', assigned_to=p['assigned_to'], supervisor_id=p['supervisor_id'],
                        submission_note=None, verification_note=None, correction_note=None, escalation_reason=None)
        elif op == 'CANCEL': task.update(status='CANCELLED', escalation_reason=p['reason'])
    return tasks


def _principal_farm_role(con, principal):
    return setup.role(con, principal)


def _can_view(role, principal_id, task):
    if role in {'OWNER', 'GENERAL_MANAGER'}: return True
    if role == 'SUPERVISOR': return task['assigned_to'] == principal_id or task['supervisor_id'] == principal_id
    return task['assigned_to'] == principal_id


def _capabilities(role, principal_id, task):
    own = task['assigned_to'] == principal_id
    supervisor = role == 'SUPERVISOR' and task['supervisor_id'] == principal_id
    manager = role in {'OWNER', 'GENERAL_MANAGER'}
    status = task['status']
    return {
        'can_acknowledge': own and status == 'ASSIGNED',
        'can_start': own and status in {'ASSIGNED', 'ACKNOWLEDGED', 'NEEDS_CORRECTION'},
        'can_submit': own and status in {'ACKNOWLEDGED', 'IN_PROGRESS', 'NEEDS_CORRECTION'},
        'can_verify': (supervisor or manager) and status == 'AWAITING_VERIFICATION',
        'can_request_correction': (supervisor or manager) and status == 'AWAITING_VERIFICATION',
        'can_escalate': (own or supervisor or manager) and status in ACTIVE_STATES - {'ESCALATED'},
        'can_reassign': manager and status not in TERMINAL_STATES,
        'can_cancel': manager and status not in TERMINAL_STATES,
    }


def overview(store, principal, offset=0):
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Invalid task page.')
    with store._connect() as con:
        principal = setup.authorize(store, con, principal, 'read')
        role = _principal_farm_role(con, principal)
        tasks = list(_snapshots(rows(con)).values())
        visible = [t for t in tasks if _can_view(role, principal.id, t)]
        visible.sort(key=lambda t: (t['status'] in TERMINAL_STATES, t['due_at'], t['task_id']))
        page = visible[offset:offset + 100]
        return {
            'tasks': [{**task, 'capabilities': _capabilities(role, principal.id, task)} for task in page],
            'total': len(visible), 'next_offset': offset + 100 if offset + 100 < len(visible) else None,
            'farm_role': role, 'can_create': role in {'OWNER', 'GENERAL_MANAGER'},
            'notice': 'Chief task state is authoritative. Device-local completion is not recorded here until submitted to Chief.'
        }


def append(store, principal, payload):
    if not isinstance(payload, dict):
        raise ValueError('Supply a task event.')
    p = dict(payload); operation = p.get('operation'); identifier(p.get('event_id'))
    if operation == 'CREATE':
        identifier(p.get('task_id'))
    else:
        common = {'event_id', 'operation', 'task_id', 'expected_revision'}
        allowed = {
            'ACKNOWLEDGE': common,
            'START': common,
            'SUBMIT': common | {'note'},
            'VERIFY': common | {'note'},
            'REQUEST_CORRECTION': common | {'note'},
            'ESCALATE': common | {'reason'},
            'REASSIGN': common | {'assigned_to', 'supervisor_id', 'reason'},
            'CANCEL': common | {'reason'},
        }
        if operation not in allowed or set(p) != allowed[operation]:
            raise ValueError('Supply exactly the fields for a known task operation.')
        identifier(p['task_id']); identifier(p['expected_revision'])
        if 'note' in p: p['note'] = bounded_text(p['note'], 1200, empty=True)
        if 'reason' in p: p['reason'] = bounded_text(p['reason'], 800)
        if operation == 'REASSIGN':
            identifier(p['assigned_to'])
            if p['supervisor_id'] is not None: identifier(p['supervisor_id'])
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = setup.authorize(store, con, principal, 'manage' if operation != 'CREATE' else 'setup')
        setup.available(store, con)
        history = rows(con)
        prior = next((r for r in history if r['payload']['event_id'] == p['event_id']), None)
        if prior:
            if prior['payload'] != p or prior['actor_id'] != principal.id:
                raise ValueError('Submission identifier conflict.')
            return {'status': 'ALREADY_RECORDED', 'revision': p['event_id']}
        tasks = _snapshots(history); role = _principal_farm_role(con, principal)
        if operation == 'CREATE':
            if role not in {'OWNER', 'GENERAL_MANAGER'}:
                raise PermissionError('Farm management authority is required to create field tasks.')
            if p['task_id'] in tasks:
                raise ValueError('Task identifier already exists.')
            _validate_create(con, p)
        else:
            task = tasks.get(p['task_id'])
            if not task:
                raise FileNotFoundError('Task not found.')
            if task['revision'] != p['expected_revision']:
                raise ValueError('Task changed elsewhere. Refresh before changing it.')
            caps = _capabilities(role, principal.id, task)
            cap_for = {
                'ACKNOWLEDGE':'can_acknowledge','START':'can_start','SUBMIT':'can_submit','VERIFY':'can_verify',
                'REQUEST_CORRECTION':'can_request_correction','ESCALATE':'can_escalate','REASSIGN':'can_reassign','CANCEL':'can_cancel'
            }[operation]
            if not caps[cap_for]:
                raise PermissionError('Current Farm role, assignment, or task state does not permit this transition.')
            if operation == 'REASSIGN':
                _target(con, p['assigned_to'])
                if p['supervisor_id'] is not None:
                    _, supervisor_role = _target(con, p['supervisor_id'])
                    if supervisor_role != 'SUPERVISOR':
                        raise ValueError('Task supervisor must have the Farm Supervisor assignment.')
                    if p['supervisor_id'] == p['assigned_to']:
                        raise ValueError('Task supervisor and assignee must differ.')
        record = {'version': 1, 'payload': p, 'actor_id': principal.id,
                  'session_id': principal.session_id, 'received_at': utc_text(utc_now())}
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",
                    (KIND, json.dumps(record, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_TASK_' + operation, 'farming', p['event_id'], 'RECORDED')
    return {'status': 'RECORDED', 'revision': p['event_id']}
