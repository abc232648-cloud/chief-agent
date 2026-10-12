from contextlib import nullcontext
"""Human-reported poultry records. No model calls, equipment effects or schema DDL.

An isolated pilot uses the existing domain_records namespace. BEGIN IMMEDIATE
serializes idempotency, corrections and insertions. This is an application-level
append-only journal, not protection against an administrator modifying SQLite.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import json
import re
import time

from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text, aware_utc


KIND = 'poultry_journal_v1'
LIMIT = 10000  # Refuse over-capacity; never silently calculate truncated totals.
TYPES = {
    'birds_opening': ('birds', 1), 'birds_arrived': ('birds', 1),
    'birds_departed': ('birds', -1), 'mortality': ('birds', -1),
    'eggs_opening': ('eggs', 1), 'eggs_collected': ('eggs', 1),
    'eggs_dispatched': ('eggs', -1), 'eggs_lost': ('eggs', -1),
    'feed_opening': ('kg', 1), 'feed_received': ('kg', 1), 'feed_used': ('kg', -1),
}
ADJUSTMENTS = {prefix+'_adjustment_'+direction: (unit, sign) for prefix,unit in [('birds','birds'),('eggs','eggs'),('feed','kg')] for direction,sign in [('in',1),('out',-1)]}
TYPES.update(ADJUSTMENTS)
FIELDS = {'event_id', 'kind', 'location', 'quantity', 'basis', 'observed_at',
          'notes', 'corrects', 'reason'}


def bounded_text(value, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError('Invalid or missing text field.')
    if any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('Control characters are not accepted.')
    return value.strip()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', value):
        raise ValueError('Use an 8–80 character record identifier.')
    return value


def validate(payload):
    if not isinstance(payload, dict) or not FIELDS <= set(payload) or set(payload) - FIELDS - {'entity_id', 'historical_before_opening', 'conversion'}:
        raise ValueError('Supply exactly the journal fields; reporter identity is supplied by the server.')
    out = dict(payload)
    if 'historical_before_opening' in out and out['historical_before_opening'] is not True:
        raise ValueError('Historical-before-opening must be explicitly true or omitted.')
    out['event_id'] = identifier(out['event_id'])
    if 'entity_id' in out:
        out['entity_id'] = identifier(out['entity_id'])
    if not isinstance(out['kind'], str) or out['kind'] not in TYPES:
        raise ValueError('Unknown farm record type.')
    out['location'] = bounded_text(out['location'], 80)
    if out['basis'] not in ('MEASURED', 'ESTIMATED'):
        raise ValueError('State whether this value was measured or estimated.')
    value = out['quantity']
    # Decimal strings avoid float rounding, booleans, exponent abuse and NaN.
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,9}(?:\.\d{1,3})?', value):
        raise ValueError('Use a non-negative decimal quantity with at most three decimal places.')
    try:
        quantity = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('Invalid quantity.') from exc
    if TYPES[out['kind']][0] != 'kg' and quantity != quantity.to_integral_value():
        raise ValueError('Bird and egg counts must be whole numbers.')
    out['quantity'] = format(quantity, 'f')
    if not isinstance(out['observed_at'], str) or len(out['observed_at']) > 40:
        raise ValueError('An observation time including its timezone is required.')
    observed = aware_utc(out['observed_at'])
    if observed > utc_now() + timedelta(minutes=5):
        raise ValueError('Observation time is in the future.')
    out['observed_at'] = utc_text(observed)
    out['notes'] = bounded_text(out['notes'], 1000, empty=True)
    out['reason'] = bounded_text(out['reason'], 400, empty=True)
    if out['corrects'] is not None:
        out['corrects'] = identifier(out['corrects'])
        if not out['reason']:
            raise ValueError('A correction requires a reason.')
    elif out['reason']:
        raise ValueError('Correction reason requires an original record.')
    return out


def read_rows(con):
    rows = con.execute('SELECT data_json FROM domain_records WHERE domain=? AND kind=? ORDER BY id LIMIT ?',
                       ('farming', KIND, LIMIT + 1)).fetchall()
    if len(rows) > LIMIT:
        raise ValueError('Pilot record capacity exceeded; export and upgrade the journal before continuing.')
    return [json.loads(row[0]) for row in rows]


def effective(rows):
    replaced = {r['payload']['corrects'] for r in rows if r['payload']['corrects']}
    return [r for r in rows if r['payload']['event_id'] not in replaced]


def append(store, principal, payload, *, connection=None):
    payload = validate(payload)
    if payload['kind'] in ADJUSTMENTS:
        raise PermissionError('Stock adjustments require the physical-count approval workflow.')
    identity = IdentityService(store)
    with (store._connect() if connection is None else nullcontext(connection)) as con:
        if connection is None:
            con.execute('BEGIN IMMEDIATE')
        elif not con.in_transaction:
            raise ValueError('A caller-owned transaction is required.')
        principal = identity.refresh(principal)
        identity.authorize(principal, 'work.request', 'farming', sensitive=False)
        from . import setup
        setup.available(store, con)
        from database.farm_indexes import schema_ready
        from . import journal_queries
        indexed = schema_ready(con)
        rows = journal_queries.stock_rows(con, payload) if indexed else read_rows(con)
        prior = journal_queries.prior(con, payload['event_id']) if indexed else next((r for r in rows if r['payload']['event_id'] == payload['event_id']), None)
        if prior:
            if prior['actor_id'] != principal.id or prior['payload'] != payload:
                raise ValueError('Record identifier already belongs to another submission.')
            return {'status': 'ALREADY_RECORDED', 'record': prior}
        from . import units
        units.check(con, payload)
        if 'entity_id' in payload:
            entity = setup.entities(con).get(payload['entity_id'])
            expected = 'FEED_STORE' if payload['kind'].startswith('feed_') else 'FLOCK'
            if not entity or entity['entity_type'] != expected:
                raise ValueError('Choose an existing entity of the correct stock type.')
            if entity['opened_on'] is not None and aware_utc(payload['observed_at']).astimezone(setup.LAGOS).date().isoformat() < entity['opened_on']:
                raise ValueError('Observation predates the entity opening date.')
            if not payload['corrects'] and payload['location'] != entity['name']:
                raise ValueError('Location must match the referenced entity name.')
        if not indexed and len(rows) >= LIMIT:
            raise ValueError('Pilot record capacity reached; upgrade required.')
        active = effective(rows)
        if payload['corrects'] is not None:
            setup.authorize(store, con, principal, 'manage')
            original = next((r for r in active if r['payload']['event_id'] == payload['corrects']), None)
            if original is None:
                raise ValueError('Correction target is missing or already corrected.')
            if bool(payload.get('historical_before_opening')) != bool(original['payload'].get('historical_before_opening')):
                raise ValueError('Corrections retain the original historical classification.')
            if payload.get('entity_id') != original['payload'].get('entity_id') or any(payload[k] != original['payload'][k] for k in ('kind', 'location', 'observed_at')):
                raise ValueError('Corrections retain the original type, location and observation time.')
        elif payload['kind'].endswith('_opening'):
            setup.authorize(store, con, principal, 'manage')
            if any(r['payload']['kind'] == payload['kind'] and stock_key(r['payload']) == stock_key(payload) for r in active):
                raise ValueError('An opening balance already exists; submit a correction instead.')
        historical = payload.get('historical_before_opening', False)
        if historical:
            setup.authorize(store, con, principal, 'manage')
            if payload['kind'].endswith('_opening'):
                raise ValueError('Historical activity cannot be another opening snapshot.')
        if payload['kind'].endswith('_opening'):
            unit = TYPES[payload['kind']][0]
            if any(stock_key(r['payload']) == stock_key(payload) and TYPES[r['payload']['kind']][0] == unit
                   and not r['payload'].get('historical_before_opening')
                   and r['payload']['observed_at'] < payload['observed_at'] for r in active):
                raise ValueError('Opening time must precede or equal the existing movements.')
        else:
            opening = next((r['payload'] for r in active if stock_key(r['payload']) == stock_key(payload)
                            and r['payload']['kind'].endswith('_opening')
                            and TYPES[r['payload']['kind']][0] == TYPES[payload['kind']][0]), None)
            if historical and (not opening or payload['observed_at'] >= opening['observed_at']):
                raise ValueError('Historical entry requires an existing later opening snapshot for this stock.')
            if not historical and opening and payload['observed_at'] < opening['observed_at']:
                raise ValueError('Movement predates the opening balance.')
        username = con.execute('SELECT username FROM human_identities WHERE id=?', (principal.id,)).fetchone()[0]
        record = {'version': 1, 'payload': payload, 'actor_id': principal.id, 'actor_name': username,
                  'session_id': principal.session_id, 'received_at': utc_text(utc_now()),
                  'evidence_status': 'USER_REPORTED'}
        con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',
                    ('farming', KIND, json.dumps(record, sort_keys=True), time.time()))
        identity._event(con, principal, 'FARM_RECORD_CORRECTED' if payload['corrects'] else 'FARM_RECORD_CREATED',
                        'farming', payload['event_id'], 'RECORDED')
    return {'status': 'RECORDED', 'record': record}


def stock_key(payload):
    return ('entity', payload['entity_id']) if 'entity_id' in payload else ('legacy', payload['location'])


def overview(store, principal, *, offset=0, limit=100):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Invalid history page.')
    IdentityService(store).authorize(principal, 'work.read', 'farming', sensitive=False)
    from . import setup
    with store._connect() as con:
        principal = IdentityService(store).refresh(principal)
        manager = setup.role(con, principal) in {'OWNER','GENERAL_MANAGER','SUPERVISOR'}
        from database.farm_indexes import schema_ready
        if schema_ready(con):
            from .journal_queries import overview as indexed_overview
            return indexed_overview(con, principal, manager, offset, limit)
        catalog = setup.entities(con)
        all_rows = read_rows(con)
        rows = []
        related = set()
        for row in all_rows:
            if manager or row['actor_id']==principal.id or row['payload']['corrects'] in related:
                rows.append(row);related.add(row['payload']['event_id'])
    groups = {}
    for record in effective(rows) if manager else []:
        p = record['payload']
        if p.get('historical_before_opening'): continue
        unit, sign = TYPES[p['kind']]
        group = groups.setdefault((stock_key(p), unit), {'location': p['location'], 'entity_id': p.get('entity_id'), 'unit': unit,
            'total': Decimal(0), 'opening_present': False, 'contains_estimates': False, 'last_observed_at': None})
        group['total'] += Decimal(p['quantity']) * sign
        group['opening_present'] |= p['kind'].endswith('_opening')
        group['contains_estimates'] |= p['basis'] == 'ESTIMATED'
        group['last_observed_at'] = max(group['last_observed_at'] or '', p['observed_at'])
    balances = []
    for _, group in sorted(groups.items()):
        if group['entity_id'] in catalog:
            group['location'] = catalog[group['entity_id']]['name']
        total = group.pop('total')
        group['recorded_balance'] = format(total, 'f') if group['opening_present'] else None
        group['needs_reconciliation'] = total < 0 if group['opening_present'] else True
        balances.append(group)
    current_ids={r['payload']['event_id'] for r in effective(all_rows)}
    page=[{**r,'is_current':r['payload']['event_id'] in current_ids} for r in list(reversed(rows))[offset:offset + limit]]
    return {'balances': balances, 'records': page, 'record_count': len(rows),
            'next_offset': offset + limit if offset + limit < len(rows) else None,
            'notice': 'Recorded balances are not live measurements. Missing records and physical counts still need review.'}
