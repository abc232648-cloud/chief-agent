"""Versioned, record-linked human clarifications. Notes are never commands.

All mutations serialize through one transaction. Existing domain validators own
stock and financial semantics. No requirements document is imported as data.
"""
from copy import deepcopy
from datetime import date
import hashlib
import json
import re
import time

from identity.service import IdentityService
from operations.time_integrity import aware_utc, utc_now, utc_text
from . import setup, journal, bookkeeping as books, planning, reconciliation

KIND = 'farm_clarification_v1'
VERSION = 3
TEMPLATES = {
    'feed': {'label': 'Feed difference', 'topic': 'Operations', 'options': {
        'actual': 'Actual use', 'correct': 'Correct an earlier entry',
        'plan': 'Change future planning baseline', 'unknown': 'Not known yet'}},
    'price': {'label': 'Sale price', 'topic': 'Finance', 'options': {
        'small': 'Small or pullet eggs', 'bulk': 'Agreed bulk price',
        'discount': 'One-off negotiated discount', 'correct': 'Correct an entry',
        'unknown': 'Not known yet'}},
    'transport': {'label': 'Transport charge', 'topic': 'Finance', 'options': {
        'delivery': 'Bird delivery', 'other_transport': 'Other transport',
        'other': 'Other charge', 'unknown': 'Not known yet'}},
    'transfer': {'label': 'Transfer status', 'topic': 'Finance', 'options': {
        'reported': 'Reported, awaiting confirmation', 'confirmed': 'Owner confirms received',
        'not_received': 'Not received', 'unknown': 'Not known yet'}},
    'stock': {'label': 'Stock quantity', 'topic': 'Operations', 'options': {
        'counted': 'Counted now', 'estimate': 'Estimate', 'unknown': 'Not available yet'}},
    'number': {'label': 'Target or number', 'topic': 'Planning', 'options': {
        'actual': 'Actual observation', 'target': 'Future planning target',
        'historical': 'Historical note', 'unknown': 'Not known yet'}},
}
KINDS = {journal.KIND, books.KIND, planning.KIND}


def _definition(con, template, source):
    definition = deepcopy(TEMPLATES[template])
    payload = source['payload']
    if template == 'price' and payload.get('details', {}).get('category') != 'EGG_SALES':
        definition['options'].pop('small')
    if template == 'price':
        definition['sale_items'] = deepcopy(payload.get('details', {}).get('items', []))
    if template == 'transfer':
        parent = next((r['payload'] for r in books.rows(con)
                       if r['payload']['event_id'] == payload['reference']), None)
        if parent is None:
            raise ValueError('Payment source is missing; review the bookkeeping record.')
        incoming = parent['kind'] in {'SALE', 'OPENING_RECEIVABLE'}
        definition['label'] = 'Incoming payment' if incoming else 'Outgoing payment'
        definition['options']['confirmed'] = 'Owner confirms money received' if incoming else 'Owner confirms payment made'
        definition['options']['not_received'] = 'Money not received' if incoming else 'Payment not made'
    return definition


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _rows(con):
    rows = con.execute("SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id LIMIT 10001", (KIND,)).fetchall()
    if len(rows) > 10000:
        raise ValueError('Clarification capacity reached; review storage before continuing.')
    return [json.loads(r[0]) for r in rows]


def _items(rows):
    result = {}
    for row in rows:
        p = row['payload']
        if p['operation'] == 'create':
            result[p['event_id']] = {'id': p['event_id'], 'template': p['template'],
                'template_version': row['version'], 'definition': row['template_definition'], 'source': p['source'], 'state': 'OPEN',
                'source_label': row['source_label'], 'revision': p['event_id'], 'creator': row['actor_id'], 'history': [row],
                'deferred_until': None, 'answer': None}
        else:
            item = result[p['item_id']]
            item['history'].append(row)
            item.update(revision=p['event_id'], state=row['state'])
            if p['operation'] == 'answer':
                item.update(answer=p, deferred_until=p['defer_until'])
            if p['operation'] == 'apply':
                item['applied'] = row['effects']
    return result


def _revision(records, kind, original):
    """Bind only the record and evidence capable of changing its meaning."""
    p = original['payload']
    if kind == journal.KIND:
        # Stock counts/adjustments depend on all movements of this stock;
        # an unrelated flock/store or egg count does not change feed evidence.
        relevant = [r for r in records if journal.stock_key(r['payload']) == journal.stock_key(p)
                    and journal.TYPES[r['payload']['kind']][0] == journal.TYPES[p['kind']][0]]
    elif kind == books.KIND:
        # Include parent, sibling payments, disputes, confirmations and voids.
        ids = {p['event_id']}
        while True:
            expanded = ids | {r['payload']['event_id'] for r in records
                              if r['payload'].get('reference') in ids}
            expanded |= {r['payload']['reference'] for r in records
                         if r['payload']['event_id'] in ids and r['payload'].get('reference')}
            if expanded == ids:
                break
            ids = expanded
        relevant = [r for r in records if r['payload']['event_id'] in ids]
    else:
        relevant = records  # Saved plans intentionally share a version chain.
    return _digest(relevant)


def _source(con, ref, *, legacy=False):
    if not isinstance(ref, dict) or set(ref) != {'kind', 'event_id', 'field', 'revision'}:
        raise ValueError('Select a source record, field and revision.')
    if not isinstance(ref['kind'], str) or ref['kind'] not in KINDS:
        raise ValueError('Unsupported Farm source.')
    journal.identifier(ref['event_id'])
    if not isinstance(ref['revision'], str) or not re.fullmatch('[0-9a-f]{64}', ref['revision']):
        raise ValueError('Refresh the source revision.')
    rows = con.execute("SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id", (ref['kind'],)).fetchall()
    records = [json.loads(r[0]) for r in rows]
    original = next((r for r in records if r['payload']['event_id'] == ref['event_id']), None)
    if original is None:
        raise ValueError('Source record is missing.')
    p = original['payload']
    allowed = {'quantity', 'basis'} if ref['kind'] == journal.KIND else ({'amount_minor', 'reason', 'reference'} if ref['kind'] == books.KIND else set(planning.INPUTS))
    if not isinstance(ref['field'], str) or ref['field'] not in allowed:
        raise ValueError('Choose a supported source field.')
    revision = _digest(records) if legacy else _revision(records, ref['kind'], original)
    active = (p['event_id'] in {r['payload']['event_id'] for r in journal.effective(records)} if ref['kind'] == journal.KIND
              else p['event_id'] in {r['payload']['event_id'] for r in books.active(records)} if ref['kind'] == books.KIND
              else p['event_id'] == records[-1]['payload']['event_id'])
    return original, revision == ref['revision'] and active


def _authorize(store, con, principal, ref, template):
    # Finance and planning clarification payloads are Owner-private. This does
    # not remove Managers' existing transaction-entry permissions elsewhere.
    principal = setup.authorize(store, con, principal, 'manage')
    if ref['kind'] in {books.KIND, planning.KIND} or TEMPLATES[template]['topic'] == 'Finance':
        if principal.role != 'Owner':
            raise PermissionError('This clarification requires the Farm Owner.')
    return principal


def _match(template, ref, source):
    p = source['payload']; kind = ref['kind']; field = ref['field']
    valid = {
        'feed': (kind == journal.KIND and p['kind'] == 'feed_used' and field == 'quantity') or (kind == planning.KIND and field == 'feed_kg_per_day'),
        'price': kind == books.KIND and p['kind'] == 'SALE' and field == 'amount_minor',
        'transport': kind == books.KIND and p['kind'] == 'EXPENSE_CLAIM' and field == 'reason',
        'transfer': kind == books.KIND and p['kind'] == 'PAYMENT_CLAIM' and field == 'reference',
        'stock': kind == journal.KIND and p['kind'].endswith('_opening') and field == 'quantity',
        'number': (kind == planning.KIND) or (kind == journal.KIND and field == 'quantity'),
    }
    if not valid[template]:
        raise ValueError('This question does not match the selected record and field.')


def _typed(item, option, data):
    if not isinstance(data, dict):
        raise ValueError('Typed follow-up fields are required.')
    template = item['template']; ref = item['source']
    if ref['kind'] == planning.KIND and option not in {'plan', 'target', 'unknown'}:
        raise ValueError('A saved plan requires a future planning answer.')
    if option == 'unknown':
        expected = set()
    elif template in {'feed', 'stock', 'number'}:
        if option == 'historical':
            expected = {'observed_at'}
        elif ref['kind'] == planning.KIND:
            if option not in {'plan', 'target'}:
                raise ValueError('A saved plan can only receive a future planning change.')
            expected = {'value', 'start', 'end'}
        else:
            if option in {'plan', 'target'}:
                raise ValueError('Choose a saved planning record for a future baseline or target.')
            expected = {'quantity', 'unit', 'observed_at'}
    elif template == 'price':
        expected = {'amount_minor', 'currency'}
        if option == 'correct' and (item['definition'].get('sale_items') or 'items' in data):
            expected.add('items')
    elif template == 'transport':
        expected = {'frequency', 'period_start', 'period_end'}
    else:
        expected = set()
    if set(data) != expected:
        raise ValueError('Complete exactly the typed follow-up fields shown for this answer.')
    if 'quantity' in data:
        if not isinstance(data['quantity'], str) or not re.fullmatch(r'\d{1,9}(?:\.\d{1,3})?', data['quantity']):
            raise ValueError('Use an exact non-negative quantity.')
        if data['unit'] not in ('eggs', 'birds', 'kg'):
            raise ValueError('Use individual eggs, birds or kilograms.')
        if data['unit'] != 'kg' and '.' in data['quantity']:
            raise ValueError('Use a whole count of birds or eggs.')
    if 'observed_at' in data:
        if not isinstance(data['observed_at'], str) or len(data['observed_at']) > 40 or aware_utc(data['observed_at']) > utc_now():
            raise ValueError('Use a past or current timezone-aware observation time.')
    if template == 'price' and option != 'unknown':
        if type(data['amount_minor']) is not int or not 0 < data['amount_minor'] <= 999999999999 or not isinstance(data['currency'], str) or data['currency'] not in books.CURRENCIES:
            raise ValueError('Supply the actual total sale amount in minor units and its currency.')
    if template == 'price' and 'items' in data:
        from .finance import details
        details({'category': 'OTHER', 'contact_id': None, 'due_on': None, 'items': data['items']},
                {'kind': 'SALE', 'amount_minor': data['amount_minor']})
        if item['definition'].get('sale_items') and not data['items']:
            raise ValueError('Preserve a reviewed item breakdown for this itemised sale.')
    if 'start' in data:
        sample = {'name': 'Clarification validation', 'start': data['start'], 'end': data['end'], 'currency': 'NGN',
                  'basis': 'Typed human answer', 'assumptions': {k: None for k in planning.INPUTS}}
        sample['assumptions'][ref['field']] = data['value']
        planning.validate(sample)
    if 'frequency' in data:
        if data['frequency'] not in ('one_off', 'recurring', 'unknown'):
            raise ValueError('Select whether the charge recurs or remains unknown.')
        for key in ('period_start', 'period_end'):
            v = data[key]
            if v is not None and (not isinstance(v, str) or date.fromisoformat(v).isoformat() != v):
                raise ValueError('Use calendar dates or leave the period unknown.')
        if data['frequency'] == 'recurring' and (not data['period_start'] or not data['period_end']):
            raise ValueError('A recurring description requires a period; no schedule is created.')
        if data['period_start'] and data['period_end'] and data['period_start'] > data['period_end']:
            raise ValueError('The period ends before it starts.')


def _apply(store, con, principal, item, source, event_id):
    answer = item['answer']; option = answer['option']; data = answer['data']; template = item['template']
    p = source['payload']; ref = item['source']; effects = []
    # Deterministic IDs bind retries to this explicit acceptance, not model text.
    event = lambda suffix: 'clarify-' + _digest([event_id, suffix])[:48]
    reason = 'Accepted clarification ' + item['id']
    if ref['kind'] == planning.KIND:
        new = {k: deepcopy(p[k]) for k in planning.FIELDS}
        new['assumptions'][ref['field']] = data['value']
        new.update(start=data['start'], end=data['end'], basis=reason,
                   event_id=event('plan'), expected_revision=p['event_id'], expected_evidence_revision=planning._evidence_revision(con))
        planning.save(store, principal, new, connection=con); effects.append(new['event_id'])
    elif template == 'number' and option == 'actual':
        from decimal import Decimal
        if data['unit'] != journal.TYPES[p['kind']][0] or Decimal(data['quantity']) != Decimal(p['quantity']) or aware_utc(data['observed_at']) != aware_utc(p['observed_at']):
            raise ValueError('This observation differs from the linked record. Correct the original Farm record before confirming its classification.')
    elif template == 'feed':
        if data['unit'] != journal.TYPES[p['kind']][0]:
            raise ValueError('The unit must match the referenced record.')
        correct = option == 'correct'
        if correct and aware_utc(data['observed_at']) != aware_utc(p['observed_at']):
            raise ValueError('A correction retains the original observation time.')
        new = {**p, 'event_id': event('journal'), 'quantity': data['quantity'], 'observed_at': data['observed_at'],
               'corrects': p['event_id'] if correct else None, 'reason': reason if correct else '', 'notes': reason}
        new.pop('conversion', None)
        if not correct:
            if aware_utc(data['observed_at']) <= aware_utc(p['observed_at']):
                raise ValueError('Actual new use must follow the linked observation. Choose Correct an earlier entry for that record.')
            for row in journal.read_rows(con):
                previous = row['payload']
                if previous['kind'] == p['kind'] and journal.stock_key(previous) == journal.stock_key(p) and aware_utc(previous['observed_at']) == aware_utc(data['observed_at']):
                    raise ValueError('A feed observation already exists at that time; review it instead of adding another.')
            new.pop('historical_before_opening', None)
            if p['kind'].endswith('_opening'):
                raise ValueError('Use Stock quantity for a fresh count; an observation is not another opening balance.')
        journal.append(store, principal, new, connection=con); effects.append(new['event_id'])
    elif template == 'stock' and option == 'estimate':
        if data['unit'] != journal.TYPES[p['kind']][0]:
            raise ValueError('The estimate unit must match the referenced stock.')
    elif template == 'stock' and option == 'counted':
        if not p.get('entity_id') or data['unit'] != journal.TYPES[p['kind']][0]:
            raise ValueError('A counted stock answer requires a registered matching stock location.')
        new = dict(event_id=event('count'), operation='COUNT', entity_id=p['entity_id'],
                   quantity=data['quantity'], unit=data['unit'], observed_at=data['observed_at'], reason=reason)
        reconciliation.append(store, principal, new, connection=con); effects.append(new['event_id'])
    elif template == 'price':
        if data['currency'] != p['currency']:
            raise ValueError('Clarification cannot convert the sale currency.')
        if data['amount_minor'] != p['amount_minor'] or ('items' in data and data['items'] != p.get('details', {}).get('items')):
            if option != 'correct':
                raise ValueError('The amount differs. Choose Correct an entry to change this sale.')
            void = dict(event_id=event('void'), kind='VOID', amount_minor=0, currency=p['currency'],
                        reference=p['event_id'], counterparty=p['counterparty'], receipt_ref='', observed_at=utc_text(utc_now()), reason=reason)
            books.append(store, principal, void, connection=con)
            replacement = {**p, 'event_id': event('sale'), 'amount_minor': data['amount_minor'], 'reason': reason}
            if p.get('details', {}).get('items') and 'items' not in data:
                raise ValueError('This itemised sale needs a reviewed breakdown. Close the historical question and create a new price question.')
            if 'items' in data:
                if not p.get('details'):
                    raise ValueError('Use the sale details workflow to introduce an item breakdown.')
                replacement['details'] = {**p['details'], 'items': deepcopy(data['items'])}
            books.append(store, principal, replacement, connection=con)
            effects.extend([void['event_id'], replacement['event_id']])
    elif template == 'transfer' and option == 'confirmed':
        confirm = dict(event_id=event('confirm'), kind='CONFIRM_PAYMENT', amount_minor=0, currency=p['currency'],
                       reference=p['event_id'], counterparty=p['counterparty'], receipt_ref='', observed_at=utc_text(utc_now()), reason=reason)
        books.append(store, principal, confirm, connection=con); effects.append(confirm['event_id'])
    # Other accepted classifications annotate this exact record only. They do
    # not manufacture transactions, permanent price rules, stock or schedules.
    return effects


def append(store, principal, payload):
    if not isinstance(payload, dict):
        raise ValueError('A clarification command is required.')
    p = deepcopy(payload); operation = p.get('operation'); journal.identifier(p.get('event_id'))
    fields = {'event_id', 'operation'}
    expected = fields | ({'template', 'source'} if operation == 'create' else
        {'item_id', 'expected_revision', 'option', 'notes', 'data', 'defer_until'} if operation == 'answer' else
        {'item_id', 'expected_revision'} if operation in {'apply', 'supersede'} else set())
    if operation not in {'create', 'answer', 'apply', 'supersede'} or set(p) != expected:
        raise ValueError('Supply exactly the clarification command fields.')
    if operation == 'answer':
        p['notes'] = journal.bounded_text(p['notes'], 1000, empty=True)
        if isinstance(p['data'], dict) and 'observed_at' in p['data']:
            value = p['data']['observed_at']
            if not isinstance(value, str) or len(value) > 40:
                raise ValueError('Use a timezone-aware observation time.')
            p['data']['observed_at'] = utc_text(aware_utc(value))
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = setup.authorize(store, con, principal, 'manage'); setup.available(store, con)
        rows = _rows(con); items = _items(rows)
        if operation == 'create':
            if not isinstance(p['template'], str) or p['template'] not in TEMPLATES:
                raise ValueError('Choose a known clarification template.')
            if not isinstance(p['source'], dict) or not isinstance(p['source'].get('kind'), str):
                raise ValueError('Choose a supported source reference.')
            principal = _authorize(store, con, principal, p['source'], p['template'])
            source, fresh = _source(con, p['source'])
            _match(p['template'], p['source'], source)
        else:
            journal.identifier(p['item_id']); journal.identifier(p['expected_revision'])
            item = items.get(p['item_id'])
            if item is None:
                raise ValueError('Clarification was not found.')
            principal = _authorize(store, con, principal, item['source'], item['template'])
            if item['template_version'] not in {1, 2, VERSION}:
                raise ValueError('This historical template requires its original version handler.')
            source, fresh = _source(con, item['source'], legacy=item['template_version'] < 3)
        prior = next((r for r in rows if r['payload']['event_id'] == p['event_id']), None)
        if prior:
            if prior['payload'] != p or prior['actor_id'] != principal.id:
                raise ValueError('Submission identifier conflict.')
            return {'status': 'ALREADY_RECORDED', 'record': prior}
        if len(rows) >= 10000:
            raise ValueError('Clarification capacity reached.')
        effects = []
        if operation == 'create':
            if not fresh:
                raise ValueError('Source changed; refresh and review before creating an item.')
            duplicate = next((i for i in items.values() if all(i['source'][k] == p['source'][k] for k in ('kind', 'event_id', 'field')) and i['template'] == p['template'] and i['state'] != 'SUPERSEDED'), None)
            if duplicate:
                return {'status': 'EXISTING_ITEM', 'item_id': duplicate['id']}
            state = 'OPEN'
        else:
            if item['revision'] != p['expected_revision'] or item['state'] == 'SUPERSEDED' or (item.get('applied') is not None and operation != 'supersede'):
                raise ValueError('This item changed or is complete; refresh before continuing.')
            if operation == 'supersede':
                state = 'SUPERSEDED'
            elif not fresh:
                raise ValueError('Source changed. Review and supersede this item before using a fresh source.')
            elif operation == 'answer':
                if not isinstance(p['option'], str) or p['option'] not in item['definition']['options'] or p['option'] not in TEMPLATES[item['template']]['options']:
                    raise ValueError('Choose a listed answer.')
                p['notes'] = journal.bounded_text(p['notes'], 1000, empty=True)
                if p['defer_until'] is not None:
                    if p['option'] != 'unknown' or not isinstance(p['defer_until'], str) or date.fromisoformat(p['defer_until']).isoformat() != p['defer_until'] or p['defer_until'] < utc_now().astimezone(setup.LAGOS).date().isoformat():
                        raise ValueError('Deferral needs an unknown answer and a current or future date.')
                _typed(item, p['option'], p['data'])
                if 'quantity' in p['data'] and item['source']['kind'] == journal.KIND and p['data']['unit'] != journal.TYPES[source['payload']['kind']][0]:
                    raise ValueError('The unit must match the referenced record.')
                state = 'OPEN' if p['option'] == 'unknown' else 'AWAITING_APPROVAL'
            else:
                if item['state'] != 'AWAITING_APPROVAL' or not item['answer'] or item['answer']['option'] == 'unknown':
                    raise ValueError('A known structured answer must be reviewed before acceptance.')
                principal = setup.authorize(store, con, principal, 'approve')
                effects = _apply(store, con, principal, item, source, p['event_id'])
                state = 'ANSWERED'
        record = {'version': VERSION, 'payload': p, 'actor_id': principal.id,
                  'session_id': principal.session_id, 'received_at': utc_text(utc_now()), 'state': state, 'effects': effects,
                  'source_kind': p['source']['kind'] if operation == 'create' else item['source']['kind']}
        if operation == 'create':
            record['template_definition'] = _definition(con, p['template'], source)
            sp = source['payload']
            record['source_label'] = ' · '.join(str(v) for v in (sp.get('location', sp.get('name', sp.get('counterparty', 'Farm record'))), sp.get('observed_at', sp.get('start', ''))))
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)", (KIND, json.dumps(record, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_CLARIFICATION_'+operation.upper(), 'farming', p.get('item_id', p['event_id']), state)
    return {'status': 'RECORDED', 'record': record}


def overview(store, principal):
    with store._connect() as con:
        con.execute('BEGIN')
        principal = setup.authorize(store, con, principal, 'read')
        if setup.role(con, principal) not in {'OWNER', 'GENERAL_MANAGER', 'SUPERVISOR'}:
            return {'items': [], 'sources': [], 'templates': {}, 'unresolved': 0, 'can_apply': False}
        items = []
        for item in _items(_rows(con)).values():
            try:
                _authorize(store, con, principal, item['source'], item['template'])
            except PermissionError:
                continue
            _, fresh = _source(con, item['source'], legacy=item['template_version'] < 3)
            item['source_changed'] = not fresh
            item['deferred'] = bool(item['answer'] and item['answer']['option'] == 'unknown' and
                (item['deferred_until'] is None or item['deferred_until'] > utc_now().astimezone(setup.LAGOS).date().isoformat()))
            items.append(item)
        sources = []
        for kind in sorted(KINDS):
            if kind != journal.KIND and principal.role != 'Owner':
                continue
            records = [json.loads(r[0]) for r in con.execute("SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id", (kind,))]
            active = journal.effective(records) if kind == journal.KIND else books.active(records) if kind == books.KIND else records[-1:]
            for record in active[-100:]:
                p = record['payload']
                fields = ['quantity'] if kind == journal.KIND else ['amount_minor', 'reason', 'reference'] if kind == books.KIND else sorted(planning.INPUTS)
                for field in fields:
                    ref = {'kind': kind, 'event_id': p['event_id'], 'field': field, 'revision': _revision(records, kind, record)}
                    for template in TEMPLATES:
                        try:
                            _match(template, ref, record)
                            _authorize(store, con, principal, ref, template)
                        except (ValueError, PermissionError):
                            continue
                        sources.append({'source': ref, 'template': template,
                            'label': ' · '.join(str(v) for v in (p.get('location', p.get('name', p.get('counterparty', 'Farm record'))), p.get('observed_at', p.get('start', '')), TEMPLATES[template]['label'], field.replace('_', ' '), p['event_id'][:8])),
                            'observed_at': p.get('observed_at'), 'unit': journal.TYPES[p['kind']][0] if kind == journal.KIND else None,
                            'currency': p.get('currency')})
        return {'items': list(reversed(items)), 'sources': sources, 'templates': TEMPLATES,
                'unresolved': sum(i['state'] in {'OPEN', 'AWAITING_APPROVAL'} for i in items), 'can_apply': principal.role == 'Owner',
                'notice': 'Answer what you know and leave the rest for later. The Owner reviews changes before they affect farm records. Notes cannot authorize payments or stock changes.'}
