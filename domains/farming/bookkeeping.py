"""Reported operational money records. No banking, purchasing or payment effects.

Approval is prospective permission for a request, never evidence of payment.
Currency is explicit and amounts use integer minor units; no currency conversion.
"""
import json
import time
from . import setup
from .journal import bounded_text, identifier
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text, aware_utc

KIND = 'farm_bookkeeping_v1'
TYPES = {'PURCHASE_REQUEST', 'APPROVE_REQUEST', 'REJECT_REQUEST', 'EXPENSE_CLAIM',
         'SALE', 'PAYMENT_CLAIM', 'CONFIRM_PAYMENT', 'VOID',
         'OPENING_RECEIVABLE', 'OPENING_PAYABLE', 'DEBT_DOCUMENT', 'DISPUTE_DEBT', 'RESOLVE_DEBT_DISPUTE'}
OBLIGATIONS = {'SALE', 'EXPENSE_CLAIM', 'OPENING_RECEIVABLE', 'OPENING_PAYABLE'}
RECEIVABLES = {'SALE', 'OPENING_RECEIVABLE'}
# Explicit supported minor-unit scale, not an inferred exchange rate.
CURRENCIES = {'NGN': 2, 'USD': 2, 'GBP': 2, 'EUR': 2}
FIELDS = {'event_id', 'kind', 'amount_minor', 'currency', 'reference', 'counterparty',
          'receipt_ref', 'observed_at', 'reason'}


def rows(con):
    return [json.loads(r[0]) for r in con.execute(
        'SELECT data_json FROM domain_records WHERE domain=? AND kind=? ORDER BY id', ('farming', KIND))]


def active(records):
    voids = {r['payload']['reference'] for r in records if r['payload']['kind'] == 'VOID'}
    return [r for r in records if r['payload']['event_id'] not in voids]


def disputed(current):
    resolved={r['reference'] for r in current.values() if r['kind']=='RESOLVE_DEBT_DISPUTE'}
    return {r['reference'] for r in current.values() if r['kind']=='DISPUTE_DEBT' and r['event_id'] not in resolved}


def validate(payload):
    if not isinstance(payload, dict) or set(payload) not in (FIELDS,FIELDS|{'details'}):
        raise ValueError('Supply exactly the bookkeeping fields.')
    p = dict(payload)
    identifier(p['event_id'])
    if not isinstance(p['kind'], str) or p['kind'] not in TYPES:
        raise ValueError('Unknown bookkeeping record.')
    if type(p['amount_minor']) is not int or not 0 <= p['amount_minor'] <= 999999999999:
        raise ValueError('Amount must be a bounded integer in currency minor units.')
    if not isinstance(p['currency'], str) or p['currency'] not in CURRENCIES:
        raise ValueError('An explicitly supported currency is required.')
    if p['reference'] is not None:
        identifier(p['reference'])
    for field, limit in [('counterparty', 120), ('receipt_ref', 120), ('reason', 400)]:
        p[field] = bounded_text(p[field], limit, empty=field != 'reason')
    if not isinstance(p['observed_at'], str) or len(p['observed_at']) > 40:
        raise ValueError('Timezone-aware observation time is required.')
    at = aware_utc(p['observed_at'])
    if at > utc_now():
        raise ValueError('Financial observations cannot be future dated.')
    p['observed_at'] = utc_text(at)
    if 'details' in p:
        from .finance import details
        p['details']=details(p['details'],p)
    return p


def append(store, principal, payload):
    p = validate(payload)
    owner_only = p['kind'] in {'APPROVE_REQUEST', 'REJECT_REQUEST', 'CONFIRM_PAYMENT', 'VOID', 'DEBT_DOCUMENT', 'RESOLVE_DEBT_DISPUTE'}
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = setup.authorize(store, con, principal, 'approve' if owner_only else 'finance')
        if principal.role != 'Owner':
            from .financial_permissions import effective
            permission = ('flag_debts' if p['kind'] == 'DISPUTE_DEBT' else
                          'report_payments' if p['kind'] == 'PAYMENT_CLAIM' else
                          'record_debts' if p['kind'] in OBLIGATIONS else None)
            if permission and not effective(con, principal.id)[permission]:
                raise PermissionError('The Owner has disabled this financial permission.')
        setup.available(store, con)
        from .finance import validate_contact
        validate_contact(con,p)
        history = rows(con)
        prior = next((r for r in history if r['payload']['event_id'] == p['event_id']), None)
        if prior:
            if prior['payload'] != p or prior['actor_id'] != principal.id:
                raise ValueError('Submission identifier conflict.')
            return {'status': 'ALREADY_RECORDED', 'record': prior}
        current = {r['payload']['event_id']: r['payload'] for r in active(history)}
        ref = current.get(p['reference'])
        if p['reference'] is not None and ref is None:
            raise ValueError('Reference is missing or voided.')
        kind = p['kind']
        if kind in {'PURCHASE_REQUEST', 'SALE', 'OPENING_RECEIVABLE', 'OPENING_PAYABLE'}:
            if ref is not None or p['amount_minor'] <= 0:
                raise ValueError('New requests and sales need a positive amount and no parent.')
        elif kind == 'EXPENSE_CLAIM':
            if p['amount_minor'] <= 0 or (ref and ref['kind'] != 'PURCHASE_REQUEST'):
                raise ValueError('Expenses may reference a purchase request; they never imply its approval.')
        elif kind in {'APPROVE_REQUEST', 'REJECT_REQUEST'}:
            if not ref or ref['kind'] != 'PURCHASE_REQUEST':
                raise ValueError('Decision requires a purchase request.')
            if any(r['reference'] == p['reference'] and r['kind'] in {'APPROVE_REQUEST', 'REJECT_REQUEST'} for r in current.values()):
                raise ValueError('Request already decided.')
            if any(r['reference'] == p['reference'] and r['kind'] == 'EXPENSE_CLAIM' for r in current.values()):
                raise ValueError('An already reported expense cannot receive retroactive purchase approval.')
            if p['amount_minor'] != 0:
                raise ValueError('Decisions carry no money movement.')
        elif kind == 'DISPUTE_DEBT':
            if not ref or ref['kind'] not in OBLIGATIONS or p['amount_minor'] != 0:
                raise ValueError('A dispute references an existing obligation and carries no amount.')
            if p['reference'] in disputed(current):raise ValueError('This obligation already has an unresolved dispute.')
        elif kind == 'RESOLVE_DEBT_DISPUTE':
            if not ref or ref['kind'] != 'DISPUTE_DEBT' or p['amount_minor'] != 0:
                raise ValueError('Resolution references a dispute and carries no amount.')
            if any(r['kind']=='RESOLVE_DEBT_DISPUTE' and r['reference']==p['reference'] for r in current.values()):
                raise ValueError('Dispute already resolved.')
        elif kind == 'DEBT_DOCUMENT':
            if not ref or ref['kind'] not in {'OPENING_RECEIVABLE', 'OPENING_PAYABLE'}:
                raise ValueError('A later invoice must reference an existing opening debt.')
            if p['amount_minor'] != ref['amount_minor'] or p['counterparty'] != ref['counterparty'] or not p['receipt_ref']:
                raise ValueError('Invoice reconciliation requires the full original amount, same counterparty and invoice reference. Partial or differing invoices need separate review.')
            if any(r['kind'] == 'DEBT_DOCUMENT' and (r['reference'] == p['reference'] or (r['receipt_ref'] == p['receipt_ref'] and r['counterparty'] == p['counterparty'])) for r in current.values()):
                raise ValueError('Opening debt or invoice is already reconciled.')
        elif kind == 'PAYMENT_CLAIM':
            if not ref or ref['kind'] not in OBLIGATIONS or p['amount_minor'] <= 0 or not p['receipt_ref']:
                raise ValueError('Payment claims require a sale, expense or opening debt, positive amount and receipt reference.')
            if any(r['kind'] == 'PAYMENT_CLAIM' and r['receipt_ref'] == p['receipt_ref'] for r in current.values()):
                raise ValueError('Receipt reference already reported; review its history.')
            claimed = sum(r['amount_minor'] for r in current.values() if r['kind'] == 'PAYMENT_CLAIM' and r['reference'] == p['reference'])
            if claimed + p['amount_minor'] > ref['amount_minor']:
                raise ValueError('Payment claims exceed the referenced amount.')
        elif kind == 'CONFIRM_PAYMENT':
            if not ref or ref['kind'] != 'PAYMENT_CLAIM' or p['amount_minor'] != 0:
                raise ValueError('Confirmation references a payment claim and carries no amount.')
            if ref['reference'] in disputed(current):raise ValueError('Resolve the debt dispute before confirming payment.')
            if any(r['kind'] == 'CONFIRM_PAYMENT' and r['reference'] == p['reference'] for r in current.values()):
                raise ValueError('Payment already confirmed.')
        elif kind == 'VOID':
            if not ref or ref['kind'] in {'VOID', 'APPROVE_REQUEST', 'REJECT_REQUEST'} or p['amount_minor'] != 0:
                raise ValueError('This record cannot be voided.')
            if any(r['reference'] == p['reference'] for r in current.values()):
                raise ValueError('Resolve dependent records before voiding their parent.')
        if ref and ref['currency'] != p['currency']:
            raise ValueError('Related records must use the same currency.')
        record = {'version': 1, 'payload': p, 'actor_id': principal.id, 'session_id': principal.session_id,
                  'received_at': utc_text(utc_now()), 'status': 'HUMAN_REPORTED'}
        con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',
                    ('farming', KIND, json.dumps(record, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_FINANCE_' + kind, 'farming', p['event_id'], 'RECORDED')
    return {'status': 'RECORDED', 'record': record}


def overview(store, principal):
    with store._connect() as con:
        principal = setup.authorize(store, con, principal, 'finance')
        history = rows(con)
        current = {r['payload']['event_id']: r['payload'] for r in active(history)}
        balances = []
        disputed_ids = disputed(current)
        payments_by_parent = {}
        confirmed_ids = {r['reference'] for r in current.values() if r['kind'] == 'CONFIRM_PAYMENT'}
        for p in current.values():
            if p['kind'] == 'PAYMENT_CLAIM':
                payments_by_parent.setdefault(p['reference'], []).append(p)
        for p in current.values():
            if p['kind'] not in OBLIGATIONS:
                continue
            payments = payments_by_parent.get(p['event_id'], [])
            confirmed = sum(r['amount_minor'] for r in payments if r['event_id'] in confirmed_ids)
            balances.append({'reference': p['event_id'], 'kind': p['kind'], 'currency': p['currency'],
                             'amount_minor': p['amount_minor'], 'disputed':p['event_id'] in disputed_ids, 'confirmed_paid_minor': confirmed,
                             'outstanding_minor': p['amount_minor'] - confirmed,
                             'unconfirmed_payment_minor': sum(r['amount_minor'] for r in payments) - confirmed})
        return {'records': list(reversed(history)), 'balances': balances,
                'can_approve': setup.role(con, principal) == 'OWNER',
                'notice': 'Reported operational bookkeeping; no payment execution or statutory accounting.'}
