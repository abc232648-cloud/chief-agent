from contextlib import nullcontext
"""Optional, versioned planning scenarios. Never money/stock events or authority.

All inputs are human-supplied assumptions, not automatically verified observations.
Saved results are immutable. New journal/bookkeeping entries flag review, rather
than silently rewriting an old forecast or treating expected receipts as cash.
"""
from datetime import date
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
import json
import re
import time

from . import setup, bookkeeping
from .journal import identifier, bounded_text
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text

KIND = 'farm_planning_scenario_v1'
VERSION = 1
DECIMALS = {'eggs_per_day', 'feed_kg_at_start', 'feed_kg_per_day'}
MONEY = {'egg_price_minor', 'customer_receipts_minor', 'supplier_payments_minor',
         'operating_expenses_minor', 'operating_budget_minor'}
INPUTS = DECIMALS | MONEY | {'eggs_to_sell'}
FIELDS = {'name', 'start', 'end', 'currency', 'assumptions', 'basis'}
SAVE_FIELDS = FIELDS | {'event_id', 'expected_revision', 'expected_evidence_revision'}


def validate(p):
    if not isinstance(p, dict) or set(p) != FIELDS:
        raise ValueError('Supply the plan name, dates, currency, assumptions and their basis.')
    bounded_text(p['name'], 120)
    bounded_text(p['basis'], 1000)
    if not isinstance(p['currency'], str) or p['currency'] not in bookkeeping.CURRENCIES:
        raise ValueError('Choose a supported currency; currencies are never combined.')
    for key in ('start', 'end'):
        value = p[key]
        if not isinstance(value, str) or len(value) != 10 or date.fromisoformat(value).isoformat() != value:
            raise ValueError('Use calendar dates in YYYY-MM-DD format.')
    days = (date.fromisoformat(p['end']) - date.fromisoformat(p['start'])).days + 1
    if not 1 <= days <= 366:
        raise ValueError('Choose an inclusive planning period of 1–366 days.')
    a = p['assumptions']
    if not isinstance(a, dict) or set(a) != INPUTS:
        raise ValueError('Supply each planning input, using null for unknown values.')
    for key, value in a.items():
        if value is None:
            continue
        if key in DECIMALS:
            if not isinstance(value, str) or not re.fullmatch(r'\d{1,9}(?:\.\d{1,3})?', value):
                raise ValueError('Quantities must be non-negative exact decimals with at most three places.')
        elif type(value) is not int or not 0 <= value <= (999999999 if key == 'eggs_to_sell' else 999999999999):
            raise ValueError('Money must be bounded integer minor units; eggs to sell must be a bounded whole count.')
    return days


def calculate(p):
    days = validate(p)
    a = p['assumptions']
    qty = lambda key: None if a[key] is None else Decimal(a[key])
    eggs, feed, daily = qty('eggs_per_day'), qty('feed_kg_at_start'), qty('feed_kg_per_day')
    production = int((eggs * days).to_integral_value(rounding=ROUND_FLOOR)) if eggs is not None else None
    feed_use = daily * days if daily is not None else None
    duration = feed / daily if feed is not None and daily is not None and daily > 0 else None
    remaining = feed - feed_use if feed is not None and feed_use is not None else None
    sales = a['eggs_to_sell'] * a['egg_price_minor'] if a['eggs_to_sell'] is not None and a['egg_price_minor'] is not None else None
    budget = a['operating_budget_minor']
    expenses = a['operating_expenses_minor']
    variance = budget - expenses if budget is not None and expenses is not None else None
    money = lambda value: None if value is None else str(value)
    notices = [
        'All figures are planning assumptions, not verified actuals or promises.',
        'Feed duration assumes constant use and no deliveries or losses.',
        'Eggs to sell is a separate assumption: collections are not automatically saleable stock.',
        'Sales value is not cash received. Supplier payments and operating costs may overlap; they are not added together.',
        'Saving does not create a sale, debt, payment, purchase approval, stock movement or notification.',
    ]
    if any(v is None for v in a.values()):
        notices.append('Some inputs are unknown; unavailable results must not be treated as zero.')
    if daily == 0:
        notices.append('Zero planned feed use does not establish unlimited feed duration.')
    if remaining is not None and remaining < 0:
        notices.append('This scenario uses more feed than the stated starting stock.')
    return {'calculation_version': VERSION, 'status': 'SCENARIO_ESTIMATE', 'days': days,
            'currency': p['currency'], 'timezone': 'Africa/Lagos', 'authority': 'NONE',
            'egg_production': {'estimated_eggs': production, 'rounding': 'Whole eggs rounded down'},
            'feed': {'estimated_use_kg': money(feed_use),
                     'days_available': format(duration.quantize(Decimal('.01'), rounding=ROUND_HALF_UP), 'f') if duration is not None else None,
                     'end_balance_kg': money(remaining)},
            'sales_value_minor': money(sales), 'customer_receipts_minor': money(a['customer_receipts_minor']),
            'supplier_payments_minor': money(a['supplier_payments_minor']),
            'operating_expenses_minor': money(expenses),
            'budget': {'operating_limit_minor': money(budget), 'remaining_minor': money(variance),
                       'status': 'NOT_SET' if budget is None else ('UNAVAILABLE' if expenses is None else ('OVER_PLAN' if variance < 0 else 'WITHIN_PLAN'))},
            'limitations': notices}


def _evidence_revision(con):
    # A bounded marker, not a claim of evidence completeness. Corrections/voids
    # append new records and therefore also trigger review of older scenarios.
    return str(con.execute("SELECT coalesce(max(id),0) FROM domain_records WHERE domain='farming' AND (kind IN ('poultry_journal_v1','farm_bookkeeping_v1') OR (kind='farm_clarification_v1' AND json_extract(data_json,'$.payload.operation')='apply' AND json_extract(data_json,'$.source_kind')!='farm_planning_scenario_v1'))").fetchone()[0])


def _latest(con):
    row = con.execute("SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id DESC LIMIT 1", (KIND,)).fetchone()
    return json.loads(row[0])['payload']['event_id'] if row else None


def preview(store, principal, p):
    with store._connect() as con:
        con.execute('BEGIN')
        setup.authorize(store, con, principal, 'finance')
        result = calculate(p)
        result['evidence_revision'] = _evidence_revision(con)
    return result


def save(store, principal, p, *, connection=None):
    if not isinstance(p, dict) or set(p) != SAVE_FIELDS:
        raise ValueError('Supply a complete reviewed plan and its revision identifiers.')
    identifier(p['event_id'])
    if p['expected_revision'] is not None:
        identifier(p['expected_revision'])
    if not isinstance(p['expected_evidence_revision'], str) or not re.fullmatch(r'\d{1,20}', p['expected_evidence_revision']):
        raise ValueError('Preview the plan before saving it.')
    with (store._connect() if connection is None else nullcontext(connection)) as con:
        if connection is None:
            con.execute('BEGIN IMMEDIATE')
        elif not con.in_transaction:
            raise ValueError('A caller-owned transaction is required.')
        principal = setup.authorize(store, con, principal, 'approve')
        setup.available(store, con)
        prior = con.execute("SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? AND json_extract(data_json,'$.payload.event_id')=?", (KIND, p['event_id'])).fetchone()
        if prior:
            old = json.loads(prior[0])
            if old['payload'] != p or old['actor_id'] != principal.id:
                raise ValueError('Submission identifier conflict.')
            return {'status': 'ALREADY_RECORDED', 'revision': p['event_id']}
        if p['expected_revision'] != _latest(con):
            raise ValueError('A plan was saved elsewhere. Refresh saved plans before saving.')
        if p['expected_evidence_revision'] != _evidence_revision(con):
            raise ValueError('Farm records changed. Review a fresh preview before saving.')
        result = calculate({k: p[k] for k in FIELDS})
        row = {'payload': p, 'result': result, 'actor_id': principal.id,
               'received_at': utc_text(utc_now()), 'evidence_revision': _evidence_revision(con)}
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)", (KIND, json.dumps(row, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_PLANNING_SCENARIO', 'farming', p['event_id'], 'RECORDED')
    return {'status': 'RECORDED', 'revision': p['event_id']}


def overview(store, principal, *, offset=0, revision=None):
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Invalid planning page.')
    if revision is not None:
        identifier(revision)
    with store._connect() as con:
        con.execute('BEGIN')
        principal = setup.authorize(store, con, principal, 'finance')
        evidence = _evidence_revision(con)
        query = "SELECT data_json FROM domain_records WHERE domain='farming' AND kind=?"
        if revision is not None:
            rows = con.execute(query+" AND json_extract(data_json,'$.payload.event_id')=? LIMIT 1", (KIND, revision)).fetchall()
            if not rows:
                raise FileNotFoundError('Saved plan not found.')
        else:
            rows = con.execute(query+' ORDER BY id DESC LIMIT 20 OFFSET ?', (KIND, offset)).fetchall()
        records = []
        for row in rows:
            value = json.loads(row[0])
            value['review_needed'] = value['evidence_revision'] != evidence
            records.append(value)
        count = con.execute("SELECT count(*) FROM domain_records WHERE domain='farming' AND kind=?", (KIND,)).fetchone()[0]
        return {'records': records, 'revision': _latest(con), 'record_count': count,
                'next_offset': offset + 20 if revision is None and offset + 20 < count else None,
                'can_save': principal.role == 'Owner', 'evidence_revision': evidence,
                'notice': 'Saved estimates retain their original assumptions and results. New farm records flag review, not automatic changes.'}
