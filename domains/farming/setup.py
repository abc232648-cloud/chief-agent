"""Farm-owned identities and assignments; never installation role grants."""
from datetime import date, timezone, timedelta
import json
import time

from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text
from .journal import bounded_text, identifier

KIND = 'farm_setup_v1'
ROLES = ('GENERAL_MANAGER', 'SUPERVISOR', 'WORKER')
LAGOS = timezone(timedelta(hours=1), 'Africa/Lagos')


def rows(con):
    return [json.loads(r[0]) for r in con.execute(
        'SELECT data_json FROM domain_records WHERE domain=? AND kind=? ORDER BY id',
        ('farming', KIND))]


def role(con, principal):
    if principal.role == 'Owner':
        return 'OWNER'
    assignments = [r for r in rows(con) if r['payload']['operation'] == 'assign_role'
                   and r['payload']['human_id'] == principal.id]
    if assignments:
        return assignments[-1]['payload']['role']
    return 'GENERAL_MANAGER' if principal.role == 'Manager' else 'WORKER'


def authorize(store, con, principal, action):
    service = IdentityService(store)
    principal = service.refresh(principal)
    service.authorize(principal, 'work.read' if action == 'read' else 'work.request', 'farming', sensitive=False)
    current = role(con, principal)
    if action in {'assign', 'approve'}:
        if current != 'OWNER':
            raise PermissionError('Only a Farm-scoped Owner may perform this operation.')
        service.authorize(principal, 'work.approve', 'farming', sensitive=True)
    elif action in {'setup', 'finance'} and current not in {'OWNER', 'GENERAL_MANAGER'}:
        raise PermissionError('Farm management authority is required.')
    elif action == 'manage' and current not in {'OWNER', 'GENERAL_MANAGER', 'SUPERVISOR'}:
        raise PermissionError('Farm supervision authority is required.')
    if action == 'finance' and current != 'OWNER':
        from .financial_permissions import effective
        if not effective(con, principal.id)['access']:
            raise PermissionError('The Owner has disabled your bookkeeping access.')
    return principal


def available(store, con):
    from database.component_state import ComponentState
    enabled = con.execute('SELECT enabled FROM agent_controls WHERE domain=?', ('farming',)).fetchone()
    relevant = {('component', 'farming'), ('component', 'farming-recorder'),
                ('capability', 'farming.records.write')}
    if not enabled or not enabled[0] or any((r['kind'], r['id']) in relevant and r['mode'] != 'ENABLED'
                                          for r in ComponentState(store).rows(con)):
        raise PermissionError('Farm record entry is disabled.')


def entities(con):
    catalog = {}
    for record in rows(con):
        p = record['payload']
        if p['operation'] == 'create_entity':
            catalog[p['entity_id']] = dict(p, revision=p['event_id'])
        elif p['operation'] == 'update_entity':
            catalog[p['entity_id']].update(name=p['name'], opened_on=p['opened_on'], revision=p['event_id'])
    return catalog


def opening_date(value):
    if value is None:
        return
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('Use an ISO opening date or leave it unknown.')
    if date.fromisoformat(value) > utc_now().astimezone(LAGOS).date():
        raise ValueError('An opening date cannot be in the future.')


def append(store, principal, payload):
    if not isinstance(payload, dict):
        raise ValueError('A setup record is required.')
    p = dict(payload)
    operation = p.get('operation')
    common = {'event_id', 'operation'}
    if operation == 'create_entity':
        if set(p) != common | {'entity_id', 'entity_type', 'name', 'opened_on', 'house_id'}:
            raise ValueError('Supply exactly the entity fields.')
        identifier(p['entity_id'])
        if p['entity_type'] not in ('HOUSE', 'FLOCK', 'FEED_STORE'):
            raise ValueError('Unknown Farm entity type.')
        p['name'] = bounded_text(p['name'], 80)
        opening_date(p['opened_on'])
        if p['house_id'] is not None:
            identifier(p['house_id'])
        action = 'setup'
    elif operation == 'update_entity':
        if set(p) != common | {'entity_id', 'expected_revision', 'name', 'opened_on', 'reason'}:
            raise ValueError('Supply exactly the entity update fields.')
        identifier(p['entity_id'])
        identifier(p['expected_revision'])
        p['name'] = bounded_text(p['name'], 80)
        p['reason'] = bounded_text(p['reason'], 400)
        opening_date(p['opened_on'])
        action = 'setup'
    elif operation == 'assign_role':
        if set(p) != common | {'human_id', 'role', 'reason'} or p['role'] not in ROLES:
            raise ValueError('Supply a known Farm role and identity.')
        identifier(p['human_id'])
        p['reason'] = bounded_text(p['reason'], 400)
        action = 'assign'
    else:
        raise ValueError('Unknown setup operation.')
    identifier(p['event_id'])
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = authorize(store, con, principal, action)
        available(store, con)
        prior = next((r for r in rows(con) if r['payload']['event_id'] == p['event_id']), None)
        if prior:
            if prior['payload'] != p or prior['actor_id'] != principal.id:
                raise ValueError('Submission identifier conflict.')
            return {'status': 'ALREADY_RECORDED', 'record': prior}
        if operation == 'create_entity':
            catalog = entities(con)
            if p['entity_id'] in catalog:
                raise ValueError('Entity identifier already exists.')
            if p['entity_type'] == 'FLOCK':
                house = catalog.get(p['house_id'])
                if not house or house['entity_type'] != 'HOUSE' or (house['opened_on'] is not None and p['opened_on'] is not None and house['opened_on'] > p['opened_on']):
                    raise ValueError('A flock requires an existing house opened on or before its opening date.')
            elif p['house_id'] is not None:
                raise ValueError('Only flocks reference a house.')
        elif operation == 'update_entity':
            catalog = entities(con)
            current = catalog.get(p['entity_id'])
            if not current or current['revision'] != p['expected_revision']:
                raise ValueError('Location changed or is missing; refresh before saving.')
            if p['opened_on'] != current['opened_on'] and p['opened_on'] is not None:
                from operations.time_integrity import aware_utc
                for row in con.execute('SELECT data_json FROM domain_records WHERE domain=? AND kind IN (?,?)', ('farming', 'poultry_journal_v1', 'farm_physical_count_v1')):
                    entry = json.loads(row[0])['payload']
                    if entry.get('entity_id') == p['entity_id'] and aware_utc(entry['observed_at']).astimezone(LAGOS).date().isoformat() < p['opened_on']:
                        raise ValueError('Opening date would contradict existing observation history.')
            parent = catalog.get(current['house_id'])
            if parent and parent['opened_on'] is not None and p['opened_on'] is not None and parent['opened_on'] > p['opened_on']:
                raise ValueError('Flock opening cannot precede its house opening.')
            if p['opened_on'] is not None and any(e['house_id'] == p['entity_id'] and e['opened_on'] is not None and e['opened_on'] < p['opened_on'] for e in catalog.values()):
                raise ValueError('House opening cannot follow an existing flock opening.')
        else:
            target = con.execute('SELECT role,domains,enabled FROM human_identities WHERE id=?', (p['human_id'],)).fetchone()
            if not target or not target['enabled'] or not ({'farming', '*'} & set(json.loads(target['domains']))):
                raise PermissionError('An enabled Farm-scoped identity is required.')
            if p['role']=='GENERAL_MANAGER' and target['role']!='Manager':
                raise ValueError('Choose a Manager-level account for a General manager assignment; no account privileges are changed automatically.')
            if target['role'] in {'Owner', 'Administrator'}:
                raise PermissionError('Farm assignment cannot alter privileged Chief identities.')
        record = {'version': 1, 'payload': p, 'actor_id': principal.id,
                  'session_id': principal.session_id, 'received_at': utc_text(utc_now())}
        con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',
                    ('farming', KIND, json.dumps(record, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_SETUP_RECORDED', 'farming', p['event_id'], 'RECORDED')
    return {'status': 'RECORDED', 'record': record}


def overview(store, principal):
    with store._connect() as con:
        principal = authorize(store, con, principal, 'read')
        current = role(con, principal)
        result = {'entities': list(entities(con).values()), 'farm_role': current,
                  'can_manage': current in {'OWNER', 'GENERAL_MANAGER', 'SUPERVISOR'},
                  'can_setup': current in {'OWNER', 'GENERAL_MANAGER'}, 'can_assign': current == 'OWNER'}
        from .financial_permissions import effective, DEFAULTS
        result['financial_permissions'] = dict(DEFAULTS) if current == 'OWNER' else effective(con, principal.id)
        result['can_finance'] = result['can_setup'] and result['financial_permissions']['access']
        if result['can_setup']:
            history = [r for r in rows(con) if r['payload']['operation'] in {'create_entity', 'update_entity'}]
            result['entity_history_count'] = len(history)
            result['entity_history'] = [{'payload': r['payload'], 'actor_id': r['actor_id'], 'received_at': r['received_at']} for r in reversed(history[-100:])]
        if current == 'OWNER':
            result['assignable_users']=[{'id':r['id'],'username':r['username'],'role':r['role']} for r in con.execute('SELECT id,username,role,domains FROM human_identities WHERE enabled=1') if r['role'] not in {'Owner','Administrator'} and {'farming','*'} & set(json.loads(r['domains']))]
            result['assignments'] = [r for r in rows(con) if r['payload']['operation'] == 'assign_role']
        return result
