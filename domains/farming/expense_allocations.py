"""Explicit expense attribution, with no additional financial or stock movement."""
import json
import time
from . import bookkeeping as books, journal, setup
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text

KIND = 'farm_expense_allocations_v1'


def snapshot(con):
    expenses = {r['payload']['event_id']: r['payload'] for r in books.active(books.rows(con))
                if r['payload']['kind'] == 'EXPENSE_CLAIM'}
    flocks = {key: value for key, value in setup.entities(con).items() if value['entity_type'] == 'FLOCK'}
    history = [json.loads(row[0]) for row in con.execute(
        "SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id", (KIND,))]
    latest = {r['payload']['expense_id']: r for r in history}
    return expenses, flocks, history, latest


def valid(expenses, flocks, payload):
    expense = expenses.get(payload['expense_id'])
    return bool(expense and all(a['flock_id'] in flocks for a in payload['allocations'])
                and sum(a['amount_minor'] for a in payload['allocations']) <= expense['amount_minor'])


def append(store, principal, payload):
    if not isinstance(payload, dict) or set(payload) != {'event_id', 'expense_id', 'expected_revision', 'allocations', 'reason'}:
        raise ValueError('Supply an expense, explicit flock amounts and a reason.')
    p = json.loads(json.dumps(payload))
    journal.identifier(p['event_id']); journal.identifier(p['expense_id'])
    if p['expected_revision'] is not None:
        journal.identifier(p['expected_revision'])
    p['reason'] = journal.bounded_text(p['reason'], 400)
    if not isinstance(p['allocations'], list) or len(p['allocations']) > 100:
        raise ValueError('Supply up to 100 flock shares; an empty list clears the allocation.')
    seen = set()
    for a in p['allocations']:
        if not isinstance(a, dict) or set(a) != {'flock_id', 'amount_minor'}:
            raise ValueError('Each share needs a flock and an amount in the expense currency.')
        journal.identifier(a['flock_id'])
        if a['flock_id'] in seen or type(a['amount_minor']) is not int or not 0 < a['amount_minor'] <= 999999999999:
            raise ValueError('Use distinct flocks and positive integer minor-unit amounts.')
        seen.add(a['flock_id'])
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = setup.authorize(store, con, principal, 'approve'); setup.available(store, con)
        expenses, flocks, history, latest = snapshot(con)
        prior = next((r for r in history if r['payload']['event_id'] == p['event_id']), None)
        if prior:
            if prior['payload'] != p or prior['actor_id'] != principal.id:
                raise ValueError('Submission identifier conflict.')
            return {'status': 'ALREADY_RECORDED'}
        current = latest.get(p['expense_id'])
        if p['expected_revision'] != (current['payload']['event_id'] if current else None):
            raise ValueError('Expense allocation changed; refresh before saving.')
        if not valid(expenses, flocks, p):
            raise ValueError('Choose a current expense and registered flocks; shares cannot exceed the expense.')
        value = {'payload': p, 'actor_id': principal.id, 'received_at': utc_text(utc_now())}
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",
                    (KIND, json.dumps(value, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_EXPENSE_ALLOCATION', 'farming', p['expense_id'], 'RECORDED')
    return {'status': 'RECORDED'}


def projection(con):
    expenses, flocks, history, latest = snapshot(con)
    items = []
    for expense in expenses.values():
        current = latest.get(expense['event_id'])
        p = current['payload'] if current else None
        usable = bool(p and valid(expenses, flocks, p))
        allocations = p['allocations'] if p else []
        items.append({'expense': expense, 'revision': p['event_id'] if p else None,
                      'allocations': allocations, 'usable': usable, 'needs_review': bool(p and not usable),
                      'unallocated_minor': expense['amount_minor'] - (sum(a['amount_minor'] for a in allocations) if usable else 0)})
    return flocks, history, items


def overview(store, principal):
    with store._connect() as con:
        con.execute('BEGIN')
        principal = setup.authorize(store, con, principal, 'finance')
        flocks, history, items = projection(con)
    return {'items': items, 'flocks': list(flocks.values()), 'history': history, 'can_edit': principal.role == 'Owner',
            'notice': 'Explicit shares of existing expenses only. No additional expense, payment or stock change. Unallocated amounts remain visible; voided expenses are excluded.'}
