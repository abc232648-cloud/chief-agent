"""Immutable human field-report intake for Chief Farm Staff.

A worker report records what a person says they observed. It does not mutate
stock balances, diagnose flock health, create incidents, dispatch work, or
create STOP/ASK authority. Specialized Chief workflows must explicitly consume
or reconcile these reports later.
"""
from decimal import Decimal, InvalidOperation
import json
import re
import time

from identity.service import IdentityService
from operations.time_integrity import aware_utc, utc_now, utc_text
from . import setup, tasks
from .journal import bounded_text, identifier

KIND = 'farm_worker_report_v1'
LIMIT = 10000
CATEGORIES = {
    'OBSERVATION', 'MORTALITY', 'FEED', 'WATER', 'EQUIPMENT',
    'INVENTORY', 'TASK_EVIDENCE'
}
SEVERITIES = {'ROUTINE', 'IMPORTANT', 'URGENT'}
BASES = {'MEASURED', 'ESTIMATED', 'COUNTED'}
FIELDS = {
    'event_id', 'category', 'task_id', 'entity_id', 'location', 'observed_at',
    'summary', 'quantity', 'unit', 'basis', 'severity'
}


def rows(con):
    values = con.execute(
        "SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id LIMIT ?",
        (KIND, LIMIT + 1),
    ).fetchall()
    if len(values) > LIMIT:
        raise ValueError('Worker-report history capacity requires indexed upgrade; partial history is not returned.')
    return [json.loads(row[0]) for row in values]


def _task_visible(con, principal, task_id):
    task = tasks._snapshots(tasks.rows(con)).get(task_id)
    if not task:
        raise ValueError('Referenced task is unavailable. Refresh tasks before reporting evidence.')
    role = setup.role(con, principal)
    if not tasks._can_view(role, principal.id, task):
        raise PermissionError('Referenced task is outside your authenticated task scope.')
    return task


def _validate(con, principal, payload):
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        raise ValueError('Supply exactly the worker-report fields.')
    p = dict(payload)
    identifier(p['event_id'])
    if p['category'] not in CATEGORIES:
        raise ValueError('Choose a supported worker-report category.')
    if p['severity'] not in SEVERITIES:
        raise ValueError('Choose a supported report severity.')

    if p['task_id'] is not None:
        identifier(p['task_id'])
        _task_visible(con, principal, p['task_id'])
    elif p['category'] == 'TASK_EVIDENCE':
        raise ValueError('Task evidence must reference an authenticated Chief task.')

    catalog = setup.entities(con)
    if p['entity_id'] is not None:
        identifier(p['entity_id'])
        entity = catalog.get(p['entity_id'])
        if not entity:
            raise ValueError('Referenced Farm entity is unavailable.')
        if p['location'] != entity['name']:
            raise ValueError('Report location must match the referenced Farm entity name.')
        if p['category'] == 'MORTALITY' and entity['entity_type'] != 'FLOCK':
            raise ValueError('Mortality observations must reference a registered flock when an entity is supplied.')
    p['location'] = bounded_text(p['location'], 120)
    p['summary'] = bounded_text(p['summary'], 2000)

    observed = aware_utc(p['observed_at'])
    if observed > utc_now():
        raise ValueError('Observation time cannot be in the future.')
    p['observed_at'] = utc_text(observed)

    if p['quantity'] is None:
        if p['unit'] is not None or p['basis'] is not None:
            raise ValueError('Unit and basis are supplied only when a quantity is reported.')
    else:
        if not isinstance(p['quantity'], str) or not re.fullmatch(r'\d{1,9}(?:\.\d{1,3})?', p['quantity']):
            raise ValueError('Reported quantity must be a non-negative decimal string with at most three decimals.')
        try:
            value = Decimal(p['quantity'])
        except InvalidOperation as exc:
            raise ValueError('Invalid reported quantity.') from exc
        if value < 0:
            raise ValueError('Reported quantity cannot be negative.')
        p['quantity'] = format(value, 'f')
        p['unit'] = bounded_text(p['unit'], 40)
        if p['basis'] not in BASES:
            raise ValueError('State whether the reported quantity was measured, estimated, or counted.')
        if p['category'] == 'MORTALITY':
            if value != value.to_integral_value() or p['unit'].lower() not in {'bird', 'birds'}:
                raise ValueError('Mortality quantity must be a whole bird count.')
    return p


def append(store, principal, payload):
    identity = IdentityService(store)
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = setup.authorize(store, con, principal, 'report')
        setup.available(store, con)
        p = _validate(con, principal, payload)
        history = rows(con)
        prior = next((record for record in history if record['payload']['event_id'] == p['event_id']), None)
        if prior:
            if prior['actor_id'] != principal.id or prior['payload'] != p:
                raise ValueError('Submission identifier conflict.')
            return {
                'status': 'ALREADY_RECEIVED',
                'report_id': p['event_id'],
                'received_at': prior['received_at'],
                'evidence_status': prior['evidence_status'],
            }
        record = {
            'version': 1,
            'payload': p,
            'actor_id': principal.id,
            'session_id': principal.session_id,
            'received_at': utc_text(utc_now()),
            'evidence_status': 'HUMAN_REPORTED',
        }
        con.execute(
            "INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",
            (KIND, json.dumps(record, sort_keys=True), time.time()),
        )
        identity._event(con, principal, 'FARM_WORKER_REPORT_RECEIVED', 'farming', p['event_id'], 'RECORDED')
    return {
        'status': 'RECEIVED',
        'report_id': p['event_id'],
        'received_at': record['received_at'],
        'evidence_status': record['evidence_status'],
    }


def overview(store, principal, offset=0):
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Invalid worker-report page.')
    with store._connect() as con:
        principal = setup.authorize(store, con, principal, 'read')
        role = setup.role(con, principal)
        visible = []
        for record in rows(con):
            if record['actor_id'] == principal.id:
                visible.append(record)
                continue
            if role in {'OWNER', 'GENERAL_MANAGER'}:
                visible.append(record)
                continue
            if role == 'SUPERVISOR' and record['payload']['task_id']:
                try:
                    task = _task_visible(con, principal, record['payload']['task_id'])
                except (PermissionError, ValueError):
                    continue
                if task['supervisor_id'] == principal.id:
                    visible.append(record)
        visible.reverse()
        page = visible[offset:offset + 100]
        return {
            'reports': page,
            'total': len(visible),
            'next_offset': offset + 100 if offset + 100 < len(visible) else None,
            'notice': (
                'Human-reported evidence only. A received worker report does not update stock balances, '
                'verify task completion, diagnose flock health, create an incident, or create STOP/ASK authority.'
            ),
        }
