"""Read-only whole-farm costing. Estimates never create payment authority."""
import hashlib
import json
from datetime import timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from . import bookkeeping as books, finance, setup
from .journal_queries import ACTIVE
from operations.time_integrity import aware_utc

LAGOS = timezone(timedelta(hours=1))


def report(store, principal, *, start, end, currency, feed_minor_per_kg=None):
    finance.day(start); finance.day(end)
    if start > end:
        raise ValueError('Start date must precede end date.')
    if currency not in books.CURRENCIES:
        raise ValueError('Choose an explicitly supported currency.')
    if feed_minor_per_kg is not None and (type(feed_minor_per_kg) is not int or not 0 <= feed_minor_per_kg <= 999999999999):
        raise ValueError('Feed valuation must be explicit integer minor units per kg or unknown.')
    def in_period(payload):
        day = aware_utc(payload['observed_at']).astimezone(LAGOS).date().isoformat()
        return start <= day <= end
    with store._connect() as con:
        con.execute('BEGIN')
        setup.authorize(store, con, principal, 'finance')
        movements = []
        for row in con.execute("SELECT data_json FROM domain_records d WHERE d.domain='farming' AND d.kind='poultry_journal_v1' AND " + ACTIVE + ' ORDER BY id'):
            payload = json.loads(row[0])['payload']
            if in_period(payload) and payload['kind'] in {'feed_used', 'eggs_collected', 'eggs_lost'}:
                movements.append(payload)
        expenses = [r['payload'] for r in books.active(books.rows(con))
                    if r['payload']['kind'] == 'EXPENSE_CLAIM' and in_period(r['payload'])]
    kg = sum((Decimal(p['quantity']) for p in movements if p['kind'] == 'feed_used'), Decimal(0))
    eggs = sum(int(Decimal(p['quantity'])) for p in movements if p['kind'] == 'eggs_collected')
    losses = sum(int(Decimal(p['quantity'])) for p in movements if p['kind'] == 'eggs_lost')
    categories = {'FEED': 0, 'BIRDS': 0, 'RECURRING': 0, 'UNCLASSIFIED': 0}
    for p in expenses:
        if p['currency'] != currency:
            continue
        category = p.get('details', {}).get('category')
        key = category if category in {'FEED', 'BIRDS'} else ('RECURRING' if category and category != 'OTHER' else 'UNCLASSIFIED')
        categories[key] += p['amount_minor']
    limitations = ['Expense and production coverage has not been certified.',
                   'Saleable production is unavailable: losses are not linked to production cohorts.',
                   'Recorded operating expenses may be one-off. Their frequency is not established and no future repeat is assumed.']
    if not any(p['kind'] == 'feed_used' for p in movements):
        limitations.append('No feed consumption records in this period; absence does not establish zero consumption.')
    if not any(p['kind'] == 'eggs_collected' for p in movements):
        limitations.append('No egg collection records in this period.')
    if feed_minor_per_kg is None:
        limitations.append('Feed valuation is not supplied.')
    if categories['UNCLASSIFIED']:
        limitations.append('Unclassified expenses are excluded from operating cost pending classification.')
    if any(p['currency'] != currency for p in expenses):
        limitations.append('Other currencies are excluded; no exchange rate is assumed.')
    if any(p['basis'] == 'ESTIMATED' for p in movements):
        limitations.append('Some quantities are estimates.')
    feed_cost = kg * feed_minor_per_kg if feed_minor_per_kg is not None and any(p['kind'] == 'feed_used' for p in movements) else None
    total = feed_cost + categories['RECURRING'] if feed_cost is not None else None
    per_egg = (total / eggs).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP) if total is not None and eggs else None
    references = [p['event_id'] for p in movements + expenses]
    identity = {'movements': movements, 'expenses': expenses, 'start': start, 'end': end,
                'currency': currency, 'feed_minor_per_kg': feed_minor_per_kg}
    return {'status': 'PROVISIONAL', 'start': start, 'end': end, 'timezone': 'Africa/Lagos',
            'currency': currency, 'feed_minor_per_kg': feed_minor_per_kg,
            'feed_consumed_kg': str(kg), 'collected_eggs': eggs, 'inventory_eggs_lost': losses,
            'saleable_eggs': None, 'saleable_cost_per_egg_minor': None,
            'feed_purchases_minor': str(categories['FEED']), 'bird_acquisition_minor': str(categories['BIRDS']),
            'recurring_expenses_minor': str(categories['RECURRING']), 'unclassified_expenses_minor': str(categories['UNCLASSIFIED']),
            'operating_expenses_minor': str(categories['RECURRING']), 'expense_recurrence': 'NOT_ESTABLISHED',
            'consumed_feed_cost_minor': str(feed_cost) if feed_cost is not None else None,
            'recorded_recurring_cost_minor': str(total) if total is not None else None,
            'cost_per_collected_egg_minor': str(per_egg) if per_egg is not None else None,
            'source_references': references, 'limitations': limitations,
            'revision': hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
            'authority': 'NONE', 'notice': 'Read-only estimate from recorded evidence; not profit, payment or spending approval.'}
