"""Read-only whole-farm costing. Estimates never create payment authority."""
from . import scope
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from . import bookkeeping as books, finance, setup, egg_loss_links, journal, expense_allocations
from operations.time_integrity import aware_utc



@scope.entry
def report(store, principal, *, start, end, currency, feed_minor_per_kg=None):
    finance.day(start); finance.day(end)
    if start > end:
        raise ValueError('Start date must precede end date.')
    if currency not in books.CURRENCIES:
        raise ValueError('Choose an explicitly supported currency.')
    if feed_minor_per_kg is not None and (type(feed_minor_per_kg) is not int or not 0 <= feed_minor_per_kg <= 999999999999):
        raise ValueError('Feed valuation must be explicit integer minor units per kg or unknown.')
    report_zone = setup.farm_zone(store)
    def in_period(payload):
        day = aware_utc(payload['observed_at']).astimezone(report_zone).date().isoformat()
        return start <= day <= end
    with store._connect() as con:
        con.execute('BEGIN')
        setup.authorize(store, con, principal, 'finance')
        movements = []
        for record in journal.effective(journal.read_rows(con)):
            payload = record['payload']
            if in_period(payload) and payload['kind'] in {'feed_used', 'eggs_collected', 'eggs_lost'}:
                movements.append(payload)
        egg_sources, link_history, loss_links = egg_loss_links.projection(con)
        flocks, allocation_history, allocated_expenses = expense_allocations.projection(con)
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
    collections = {p['event_id']:p for p in movements if p['kind']=='eggs_collected'}
    relevant = [r for r in loss_links if any(journal.stock_key(r['loss']) == journal.stock_key(c)
                and aware_utc(r['loss']['observed_at']) >= aware_utc(c['observed_at']) for c in collections.values())]
    linked = sum(a['eggs'] for r in relevant if r['usable'] for a in r['allocations'] if a['collection_id'] in collections)
    unallocated = sum(r['unallocated'] for r in relevant)
    reviewed = bool(relevant) and not unallocated and not any(r['needs_review'] for r in relevant)
    saleable = eggs-linked if reviewed else None
    saleable_cost = (total/saleable).quantize(Decimal('0.0001'),rounding=ROUND_HALF_UP) if total is not None and saleable else None
    limitations.append('Collection-period estimate after explicitly linked losses, including later recorded losses. This does not certify egg grading, complete loss reporting or current saleable stock.')
    if not reviewed: limitations.append('Saleable cost unavailable: loss attribution is incomplete or has not been established. Unknown losses are not assigned automatically.')
    flock_totals = {}
    unallocated_expenses = 0
    allocation_refs = []
    for item in allocated_expenses:
        expense = item['expense']
        if expense['currency'] != currency or not in_period(expense):
            continue
        unallocated_expenses += item['unallocated_minor']
        if item['revision']:
            allocation_refs.append(item['revision'])
        if item['usable']:
            for share in item['allocations']:
                flock_id = share['flock_id']
                flock_totals[flock_id] = flock_totals.get(flock_id, 0) + share['amount_minor']
    limitations.append('Flock figures attribute recorded expense claims only, including feed purchases where allocated. They are not added to consumed-feed valuation and do not establish total flock cost or profit. No currency conversion or automatic allocation is performed.')
    references = [p['event_id'] for p in movements + expenses] + allocation_refs
    references += [r['loss']['event_id'] for r in relevant] + [r['revision'] for r in relevant if r['revision']]
    identity = {'movements': movements, 'expenses': expenses, 'start': start, 'end': end,
                'currency': currency, 'feed_minor_per_kg': feed_minor_per_kg, 'loss_links': relevant, 'link_history':link_history,
                'expense_allocations': allocated_expenses, 'allocation_history': allocation_history, 'flocks': flocks}
    return {'status': 'PROVISIONAL', 'start': start, 'end': end, 'timezone': report_zone.key,
            'currency': currency, 'feed_minor_per_kg': feed_minor_per_kg,
            'flock_expenses': [{'flock_id': key, 'name': flocks[key]['name'], 'allocated_minor': amount}
                              for key, amount in sorted(flock_totals.items())],
            'unallocated_expenses_minor': unallocated_expenses,
            'feed_consumed_kg': str(kg), 'collected_eggs': eggs, 'inventory_eggs_lost': losses,
            'saleable_eggs': saleable, 'saleable_cost_per_egg_minor': str(saleable_cost) if saleable_cost is not None else None,
            'linked_losses_from_collections':linked, 'unallocated_relevant_losses':unallocated, 'loss_attribution':'RECORDED_LINKS' if reviewed else 'INCOMPLETE',
            'feed_purchases_minor': str(categories['FEED']), 'bird_acquisition_minor': str(categories['BIRDS']),
            'recurring_expenses_minor': str(categories['RECURRING']), 'unclassified_expenses_minor': str(categories['UNCLASSIFIED']),
            'operating_expenses_minor': str(categories['RECURRING']), 'expense_recurrence': 'NOT_ESTABLISHED',
            'consumed_feed_cost_minor': str(feed_cost) if feed_cost is not None else None,
            'recorded_recurring_cost_minor': str(total) if total is not None else None,
            'cost_per_collected_egg_minor': str(per_egg) if per_egg is not None else None,
            'source_references': references, 'limitations': limitations,
            'revision': hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
            'authority': 'NONE', 'notice': 'Read-only estimate from recorded evidence; not profit, payment or spending approval.'}
