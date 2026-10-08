"""Versioned Farm field SOPs exposed to Staff only through authoritative task scope.

Published SOP versions are immutable. Tasks refer to a pinned ``sop_id@version``;
the assigned endpoint never substitutes a newer version or invents unavailable
instructions.
"""
import json
import re
import time

from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text
from . import setup, tasks
from .journal import bounded_text, identifier

KIND = 'farm_sop_v1'
LIMIT = 5000
_VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,39}')


def version(value):
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ValueError('Use a 1–40 character SOP version.')
    return value


def reference(sop_id, sop_version):
    return identifier(sop_id) + '@' + version(sop_version)


def parse_reference(value):
    if not isinstance(value, str) or value.count('@') != 1:
        raise ValueError('SOP references must pin an exact sop_id@version.')
    sop_id, sop_version = value.split('@', 1)
    return identifier(sop_id), version(sop_version)


def rows(con):
    result = con.execute(
        "SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id LIMIT ?",
        (KIND, LIMIT + 1),
    ).fetchall()
    if len(result) > LIMIT:
        raise ValueError('SOP catalog capacity requires indexed upgrade; no truncated catalog returned.')
    return [json.loads(row[0]) for row in result]


def catalog(history):
    result = {}
    for record in history:
        payload = record['payload']
        key = (payload['sop_id'], payload['version'])
        result[key] = {
            'sop_id': payload['sop_id'],
            'version': payload['version'],
            'title': payload['title'],
            'purpose': payload['purpose'],
            'steps': list(payload['steps']),
            'warnings': list(payload['warnings']),
            'updated_at': record['received_at'],
        }
    return result


def _text_list(value, *, limit, item_limit, name):
    if not isinstance(value, list) or not 1 <= len(value) <= limit:
        raise ValueError(f'{name} must contain between 1 and {limit} entries.')
    return [bounded_text(item, item_limit) for item in value]


def publish(store, principal, payload):
    if not isinstance(payload, dict):
        raise ValueError('Supply an SOP publication.')
    p = dict(payload)
    required = {'event_id', 'operation', 'sop_id', 'version', 'title', 'purpose', 'steps', 'warnings'}
    if set(p) != required or p.get('operation') != 'PUBLISH':
        raise ValueError('Supply exactly the SOP publication fields.')
    identifier(p['event_id']); identifier(p['sop_id']); p['version'] = version(p['version'])
    p['title'] = bounded_text(p['title'], 160)
    p['purpose'] = bounded_text(p['purpose'], 1000)
    p['steps'] = _text_list(p['steps'], limit=40, item_limit=1000, name='SOP steps')
    if not isinstance(p['warnings'], list) or len(p['warnings']) > 20:
        raise ValueError('SOP warnings must be a list with at most 20 entries.')
    p['warnings'] = [bounded_text(item, 800) for item in p['warnings']]

    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = setup.authorize(store, con, principal, 'setup')
        setup.available(store, con)
        history = rows(con)
        prior_event = next((r for r in history if r['payload']['event_id'] == p['event_id']), None)
        if prior_event:
            if prior_event['payload'] != p or prior_event['actor_id'] != principal.id:
                raise ValueError('Submission identifier conflict.')
            return {'status': 'ALREADY_RECORDED', 'sop_ref': reference(p['sop_id'], p['version']),
                    'updated_at': prior_event['received_at']}
        prior_version = next((r for r in history if r['payload']['sop_id'] == p['sop_id'] and r['payload']['version'] == p['version']), None)
        if prior_version:
            raise ValueError('Published SOP versions are immutable; publish a new version instead.')
        received_at = utc_text(utc_now())
        record = {'version': 1, 'payload': p, 'actor_id': principal.id,
                  'session_id': principal.session_id, 'received_at': received_at}
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",
                    (KIND, json.dumps(record, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_SOP_PUBLISHED', 'farming', p['event_id'], 'RECORDED')
    return {'status': 'RECORDED', 'sop_ref': reference(p['sop_id'], p['version']), 'updated_at': received_at}


def overview(store, principal):
    """Management catalog. The Staff application uses ``assigned`` instead."""
    with store._connect() as con:
        principal = setup.authorize(store, con, principal, 'setup')
        items = list(catalog(rows(con)).values())
    items.sort(key=lambda item: (item['sop_id'], item['version']))
    return {'sops': items, 'total': len(items),
            'notice': 'Published SOP versions are immutable. Assign tasks using an exact sop_id@version reference.'}


def assigned(store, principal):
    # Ask the authoritative task projection what this principal can see; this
    # preserves Worker/Supervisor/Manager organizational scoping in one place.
    task_view = tasks.overview(store, principal)
    refs = []
    invalid_refs = []
    for task in task_view['tasks']:
        value = task.get('sop_ref')
        if value is None:
            continue
        try:
            pinned = parse_reference(value)
        except ValueError:
            invalid_refs.append(value)
            continue
        if pinned not in refs:
            refs.append(pinned)

    with store._connect() as con:
        setup.authorize(store, con, principal, 'read')
        available = catalog(rows(con))
    result = []
    unavailable = list(invalid_refs)
    for key in refs:
        item = available.get(key)
        if item is None:
            unavailable.append(reference(*key))
        elif item not in result:
            result.append(item)
    result.sort(key=lambda item: (item['title'].lower(), item['sop_id'], item['version']))
    notice = 'Only exact SOP versions referenced by tasks in your Chief-authorized scope are returned.'
    if unavailable:
        notice += ' Some task SOP references are unavailable or unversioned and were not substituted.'
    return {'sops': result, 'total': len(result), 'notice': notice}
