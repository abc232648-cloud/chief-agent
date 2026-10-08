"""Authoritative Farm incident and emergency ledger for Staff PWA.

Incidents are append-only Chief records. CREATE and ESCALATE are idempotent by
``event_id`` and escalation uses ``expected_revision`` so an offline/stale
client cannot silently overwrite newer authoritative state.
"""
import json
import time

from identity.service import IdentityService
from operations.time_integrity import aware_utc, utc_now, utc_text
from . import setup
from .journal import bounded_text, identifier

KIND = 'farm_incident_v1'
LIMIT = 10000
KINDS = {'INCIDENT', 'EMERGENCY'}
SEVERITIES = {'IMPORTANT', 'URGENT', 'CRITICAL'}


def rows(con):
    result = con.execute(
        "SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id LIMIT ?",
        (KIND, LIMIT + 1),
    ).fetchall()
    if len(result) > LIMIT:
        raise ValueError('Incident history capacity requires indexed upgrade; no truncated incident state returned.')
    return [json.loads(row[0]) for row in result]


def _snapshots(history):
    incidents = {}
    for record in history:
        p = record['payload']; operation = p['operation']
        if operation == 'CREATE':
            incident_id = p['event_id']
            incidents[incident_id] = {
                'incident_id': incident_id,
                'kind': p['kind'],
                'category': p['category'],
                'location': p['location'],
                'observed_at': p['observed_at'],
                'summary': p['summary'],
                'severity': p['severity'],
                'immediate_risk': p['immediate_risk'],
                'status': 'OPEN',
                'revision': p['event_id'],
                'actor_id': record['actor_id'],
                'received_at': record['received_at'],
                'escalation_reason': None,
                'escalated_by': None,
                'escalated_at': None,
            }
            continue
        incident = incidents.get(p['incident_id'])
        if incident and operation == 'ESCALATE':
            incident.update(
                status='ESCALATED',
                revision=p['event_id'],
                escalation_reason=p['reason'],
                escalated_by=record['actor_id'],
                escalated_at=record['received_at'],
            )
    return incidents


def _can_view(role, principal_id, incident):
    if role in {'OWNER', 'GENERAL_MANAGER', 'SUPERVISOR'}:
        return True
    return incident['actor_id'] == principal_id


def _can_escalate(role, principal_id, incident):
    if incident['status'] == 'ESCALATED':
        return False
    return role in {'OWNER', 'GENERAL_MANAGER', 'SUPERVISOR'}


def _validate_create(p):
    required = {'event_id', 'operation', 'kind', 'category', 'location', 'observed_at', 'summary', 'severity', 'immediate_risk'}
    if set(p) != required or p.get('operation') != 'CREATE':
        raise ValueError('Supply exactly the incident creation fields.')
    identifier(p['event_id'])
    if p['kind'] not in KINDS:
        raise ValueError('Choose INCIDENT or EMERGENCY.')
    if p['severity'] not in SEVERITIES:
        raise ValueError('Choose a known incident severity.')
    if type(p['immediate_risk']) is not bool:
        raise ValueError('Immediate-risk must be a boolean.')
    p['category'] = bounded_text(p['category'], 120)
    p['location'] = bounded_text(p['location'], 160, empty=True)
    p['summary'] = bounded_text(p['summary'], 2000)
    if not isinstance(p['observed_at'], str) or len(p['observed_at']) > 40:
        raise ValueError('Incident observation time including timezone is required.')
    observed = aware_utc(p['observed_at'])
    if observed > utc_now():
        raise ValueError('Incident observation time cannot be in the future.')
    p['observed_at'] = utc_text(observed)


def overview(store, principal, offset=0):
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Invalid incident page.')
    with store._connect() as con:
        principal = setup.authorize(store, con, principal, 'read')
        role = setup.role(con, principal)
        visible = [i for i in _snapshots(rows(con)).values() if _can_view(role, principal.id, i)]
        visible.sort(key=lambda i: (i['status'] == 'ESCALATED', i['observed_at'], i['incident_id']), reverse=True)
        page = visible[offset:offset + 100]
        return {
            'incidents': [{**item, 'can_escalate': _can_escalate(role, principal.id, item)} for item in page],
            'total': len(visible),
            'next_offset': offset + 100 if offset + 100 < len(visible) else None,
            'notice': 'Chief incident state is authoritative. Device-local or queued reports are not treated as received until Chief records them.',
        }


def append(store, principal, payload):
    if not isinstance(payload, dict):
        raise ValueError('Supply an incident event.')
    p = dict(payload); operation = p.get('operation')
    if operation == 'CREATE':
        _validate_create(p)
    elif operation == 'ESCALATE':
        required = {'event_id', 'operation', 'incident_id', 'expected_revision', 'reason'}
        if set(p) != required:
            raise ValueError('Supply exactly the incident escalation fields.')
        identifier(p['event_id']); identifier(p['incident_id']); identifier(p['expected_revision'])
        p['reason'] = bounded_text(p['reason'], 800)
    else:
        raise ValueError('Unknown incident operation.')

    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = setup.authorize(store, con, principal, 'task')
        setup.available(store, con)
        history = rows(con)
        prior = next((r for r in history if r['payload']['event_id'] == p['event_id']), None)
        if prior:
            if prior['payload'] != p or prior['actor_id'] != principal.id:
                raise ValueError('Submission identifier conflict.')
            incident_id = p['event_id'] if operation == 'CREATE' else p['incident_id']
            return {'status': 'ALREADY_RECORDED', 'incident_id': incident_id,
                    'received_at': prior['received_at'], 'revision': p['event_id']}

        incidents = _snapshots(history); role = setup.role(con, principal)
        if operation == 'CREATE':
            incident_id = p['event_id']
        else:
            incident_id = p['incident_id']; incident = incidents.get(incident_id)
            if not incident:
                raise FileNotFoundError('Incident not found.')
            if incident['revision'] != p['expected_revision']:
                raise ValueError('Incident changed elsewhere. Refresh before escalating it.')
            if not _can_escalate(role, principal.id, incident):
                raise PermissionError('Current Farm role or incident scope does not permit escalation.')

        received_at = utc_text(utc_now())
        record = {'version': 1, 'payload': p, 'actor_id': principal.id,
                  'session_id': principal.session_id, 'received_at': received_at}
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",
                    (KIND, json.dumps(record, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_INCIDENT_' + operation, 'farming', p['event_id'], 'RECORDED')
    return {'status': 'RECORDED', 'incident_id': incident_id, 'received_at': received_at, 'revision': p['event_id']}
