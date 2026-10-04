"""Owner-managed Farm recipients. No external delivery integration is installed."""
import hashlib
import json
import os
import time

from identity.service import IdentityService
from operations.time_integrity import aware_utc, utc_now, utc_text
from . import setup
from .journal import identifier

CONFIG = 'farm_notification_recipients_v1'
DELIVERY = 'farm_notification_delivery_v1'


def rows(con, kind):
    return [json.loads(r[0]) for r in con.execute(
        'SELECT data_json FROM domain_records WHERE domain=? AND kind=? ORDER BY id', ('farming', kind))]


def eligible(con):
    assignments = {r['payload']['human_id']: r['payload']['role'] for r in setup.rows(con)
                   if r['payload']['operation'] == 'assign_role'}
    return {r['id']: {'id': r['id'], 'username': r['username'], 'role': r['role']}
            for r in con.execute('SELECT id,username,role,domains FROM human_identities WHERE enabled=1')
            if {'farming', '*'} & set(json.loads(r['domains']))
            and (r['role'] == 'Owner' or (r['role'] == 'Manager' and assignments.get(r['id'], 'GENERAL_MANAGER') == 'GENERAL_MANAGER'))}


def overview(store, principal):
    with store._connect() as con:
        principal = setup.authorize(store, con, principal, 'read')
        if principal.role != 'Owner':
            raise PermissionError('Only the Owner may manage notification recipients.')
        history = rows(con, CONFIG)
        latest = history[-1] if history else None
        return {'revision': latest['payload']['event_id'] if latest else None,
                'manager_ids': latest['payload']['manager_ids'] if latest else [],
                'eligible': list(eligible(con).values()),
                'effective_at': latest['received_at'] if latest else None,
                'external_delivery': 'NOT_CONFIGURED',
                'notice': 'Owners receive operational and sensitive financial notices. Selected managers receive operational notices only. Email, SMS and push delivery are not configured.'}


def configure(store, principal, payload):
    if not isinstance(payload, dict) or set(payload) != {'event_id', 'expected_revision', 'manager_ids'}:
        raise ValueError('Supply exactly the recipient settings.')
    identifier(payload['event_id'])
    if payload['expected_revision'] is not None:
        identifier(payload['expected_revision'])
    chosen = payload['manager_ids']
    if not isinstance(chosen, list) or len(chosen) > 100:
        raise ValueError('Choose at most 100 managers.')
    for human in chosen:
        identifier(human)
    if len(set(chosen)) != len(chosen):
        raise ValueError('Choose each manager once.')
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = setup.authorize(store, con, principal, 'approve')
        setup.available(store, con)
        history = rows(con, CONFIG)
        prior = next((r for r in history if r['payload']['event_id'] == payload['event_id']), None)
        if prior:
            if prior['payload'] != payload or prior['actor_id'] != principal.id:
                raise ValueError('Submission identifier conflict.')
            return {'status': 'ALREADY_RECORDED', 'effective_at': prior['received_at']}
        if payload['expected_revision'] != (history[-1]['payload']['event_id'] if history else None):
            raise ValueError('Recipient settings changed; refresh before saving.')
        users = eligible(con)
        if any(h not in users or users[h]['role'] != 'Manager' for h in chosen):
            raise ValueError('Choose enabled Farm General Managers only.')
        record = {'payload': payload, 'actor_id': principal.id, 'received_at': utc_text(utc_now())}
        con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',
                    ('farming', CONFIG, json.dumps(record, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_RECIPIENTS_CONFIGURED', 'farming', payload['event_id'], 'RECORDED')
    return {'status': 'RECORDED', 'effective_at': record['received_at']}


def plan(store, principal, event):
    """Internal metadata-only plan; never a delivery or permission grant."""
    if not isinstance(event, dict) or set(event) != {'id', 'category', 'observed_at', 'historical'}:
        raise ValueError('Supply a notification reference, category, source time and history flag.')
    identifier(event['id'])
    if event['category'] not in {'OPERATIONAL', 'FINANCIAL'} or type(event['historical']) is not bool:
        raise ValueError('Invalid notification category or history flag.')
    observed = aware_utc(event['observed_at'])
    if observed > utc_now():
        raise ValueError('Future notification source times are not accepted.')
    with store._connect() as con:
        setup.authorize(store, con, principal, 'approve')
        setup.available(store, con)
        history = rows(con, CONFIG)
        if not history or event['historical'] or observed < aware_utc(history[-1]['received_at']):
            return []
        users = eligible(con)
        selected = set(history[-1]['payload']['manager_ids'])
        return [{'recipient': h, 'category': event['category'], 'reference': event['id']}
                for h, user in users.items()
                if user['role'] == 'Owner' or (event['category'] == 'OPERATIONAL' and h in selected)]


def synthetic_delivery(store, principal, event, *, fail=False):
    """Exercise states without calling a network adapter. Test instances only."""
    if os.environ.get('CHIEF_INSTANCE_MODE') != 'test':
        raise PermissionError('Synthetic delivery is available only in isolated tests.')
    destinations = plan(store, principal, event)
    results = []
    for target in destinations:
        key = hashlib.sha256(json.dumps(target, sort_keys=True).encode()).hexdigest()
        with store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            setup.authorize(store, con, principal, 'approve')
            # Recheck identity and configuration at dispatch time, not just planning.
            if target not in plan(store, principal, event):
                continue
            history = [r for r in rows(con, DELIVERY) if r['id'] == key]
            if history and history[-1]['status'] in {'SENT', 'PENDING'}:
                results.append({'id': key, 'status': history[-1]['status']})
                continue  # An ambiguous pending result is never automatically replayed.
            for state in ('PENDING', 'FAILED' if fail else 'SENT'):
                record = {'id': key, **target, 'status': state, 'adapter': 'SYNTHETIC', 'received_at': utc_text(utc_now())}
                con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',
                            ('farming', DELIVERY, json.dumps(record, sort_keys=True), time.time()))
            results.append({'id': key, 'status': state})
    return results
