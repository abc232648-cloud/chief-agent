"""Explicit loss attribution; never writes stock or financial movements."""
from . import scope
import json
import time
from decimal import Decimal
from . import journal, setup
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text, aware_utc

KIND = 'farm_egg_loss_links_v1'


def snapshot(con):
    records = journal.effective(journal.read_rows(con))
    eggs = {r['payload']['event_id']: r['payload'] for r in records
            if r['payload']['kind'] in {'eggs_collected', 'eggs_lost'}}
    history = scope.records(con, KIND)
    latest = {}
    for r in history:
        latest[r['payload']['loss_id']] = r
    return eggs, history, latest


def valid(eggs, p):
    loss = eggs.get(p['loss_id'])
    if not loss or loss['kind'] != 'eggs_lost':
        return False
    total = 0
    for a in p['allocations']:
        collection = eggs.get(a['collection_id'])
        if not collection or collection['kind'] != 'eggs_collected':
            return False
        if journal.stock_key(loss) != journal.stock_key(collection) or aware_utc(collection['observed_at']) > aware_utc(loss['observed_at']):
            return False
        total += a['eggs']
    return total <= int(Decimal(loss['quantity']))


@scope.entry
def append(store, principal, payload):
    if not isinstance(payload, dict) or set(payload) != {'event_id','loss_id','expected_revision','allocations','reason'}:
        raise ValueError('Supply a loss, explicit collection links and a reason.')
    p = json.loads(json.dumps(payload))
    journal.identifier(p['event_id']); journal.identifier(p['loss_id'])
    if p['expected_revision'] is not None: journal.identifier(p['expected_revision'])
    p['reason'] = journal.bounded_text(p['reason'], 400)
    if not isinstance(p['allocations'], list) or len(p['allocations']) > 100:
        raise ValueError('Supply up to 100 collection links; an empty list removes the links.')
    seen = set()
    for a in p['allocations']:
        if not isinstance(a, dict) or set(a) != {'collection_id','eggs'}:
            raise ValueError('Each link needs a collection and an egg count.')
        journal.identifier(a['collection_id'])
        if a['collection_id'] in seen or type(a['eggs']) is not int or not 0 < a['eggs'] <= 999999999:
            raise ValueError('Use distinct collections and positive whole egg counts.')
        seen.add(a['collection_id'])
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = setup.authorize(store, con, principal, 'approve'); setup.available(store, con)
        eggs, history, latest = snapshot(con)
        prior = next((r for r in history if r['payload']['event_id'] == p['event_id']), None)
        if prior:
            if prior['payload'] != p or prior['actor_id'] != principal.id: raise ValueError('Submission identifier conflict.')
            return {'status':'ALREADY_RECORDED'}
        current = latest.get(p['loss_id'])
        if p['expected_revision'] != (current['payload']['event_id'] if current else None):
            raise ValueError('Loss links changed; refresh before saving.')
        if not valid(eggs, p): raise ValueError('Choose current collection records from the same stock, before the loss; linked eggs cannot exceed the loss.')
        totals = {}
        for loss_id, record in latest.items():
            if loss_id == p['loss_id'] or not valid(eggs, record['payload']): continue
            for a in record['payload']['allocations']: totals[a['collection_id']] = totals.get(a['collection_id'],0) + a['eggs']
        for a in p['allocations']:
            if totals.get(a['collection_id'],0) + a['eggs'] > int(Decimal(eggs[a['collection_id']]['quantity'])):
                raise ValueError('Loss links exceed the eggs in this collection.')
        value = {'payload':p,'actor_id':principal.id,'received_at':utc_text(utc_now())}
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",(KIND,json.dumps(scope.stamp(value),sort_keys=True),time.time()))
        IdentityService(store)._event(con,principal,'FARM_EGG_LOSS_LINKS','farming',p['loss_id'],'RECORDED')
    return {'status':'RECORDED'}


def projection(con):
    eggs, history, latest = snapshot(con)
    totals = {}
    for r in latest.values():
        if valid(eggs,r['payload']):
            for a in r['payload']['allocations']: totals[a['collection_id']] = totals.get(a['collection_id'],0) + a['eggs']
    items = []
    for loss in eggs.values():
        if loss['kind'] != 'eggs_lost': continue
        record = latest.get(loss['event_id']); p = record['payload'] if record else None
        usable = bool(p and valid(eggs,p) and all(totals[a['collection_id']] <= int(Decimal(eggs[a['collection_id']]['quantity'])) for a in p['allocations']))
        allocated = sum(a['eggs'] for a in p['allocations']) if usable else 0
        items.append({'loss':loss,'revision':p['event_id'] if p else None,'allocations':p['allocations'] if p else [],
                      'usable':usable,'needs_review':bool(p and not usable),'unallocated':int(Decimal(loss['quantity']))-allocated})
    return eggs, history, items


@scope.entry
def overview(store, principal):
    with store._connect() as con:
        con.execute('BEGIN'); principal = setup.authorize(store,con,principal,'finance')
        eggs, history, items = projection(con)
    return {'items':items,'collections':[p for p in eggs.values() if p['kind']=='eggs_collected'],
            'history':history,'can_edit':principal.role=='Owner',
            'notice':'Link existing loss records only. This never deducts stock again. Unknown sources remain unallocated; corrected source records require review.'}
