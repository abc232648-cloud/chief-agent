"""Versioned Owner costing settings; not valuation inferred from cash purchases."""
import json
import re
import time
from decimal import Decimal
from . import setup, bookkeeping
from .journal import identifier, bounded_text
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text

KIND='farm_costing_policy_v1'
FIELDS={'event_id','expected_revision','currency','feed_minor_per_kg','eggs_per_crate','kg_per_bag','reason'}


def rows(con):
    return [json.loads(r[0]) for r in con.execute("SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id",(KIND,))]


def configure(store,principal,p):
    if not isinstance(p,dict) or set(p) not in (FIELDS, FIELDS | {'price_basis','price_source'}):raise ValueError('Supply exactly the costing settings.')
    identifier(p['event_id'])
    if p['expected_revision'] is not None:identifier(p['expected_revision'])
    bounded_text(p['reason'],400)
    if p['currency'] not in bookkeeping.CURRENCIES:raise ValueError('Choose a supported currency.')
    price=p['feed_minor_per_kg']
    if price is not None and (type(price) is not int or not 0<=price<=999999999999):raise ValueError('Feed valuation must be exact minor units or unknown.')
    basis=p.get('price_basis', 'UNKNOWN' if price is None else 'UNCLASSIFIED')
    source=p.get('price_source','')
    bounded_text(source,400,empty=True)
    if 'price_basis' in p:
        if basis not in {'UNKNOWN','RECORDED_PRICE','PLANNING_ESTIMATE'}:raise ValueError('Choose unknown, recorded price or optional planning estimate.')
        if (price is None) != (basis == 'UNKNOWN'):raise ValueError('A missing price must stay unknown; a supplied price needs an explicit basis.')
        if basis == 'RECORDED_PRICE' and not source.strip():raise ValueError('A recorded price requires its document or record reference.')
    eggs=p['eggs_per_crate']
    if eggs is not None and (type(eggs) is not int or not 1<=eggs<=10000):raise ValueError('Crate size must be a positive whole egg count or unknown.')
    kg=p['kg_per_bag']
    if kg is not None and (not isinstance(kg,str) or not re.fullmatch(r'\d{1,6}(?:\.\d{1,3})?',kg) or Decimal(kg)<=0):raise ValueError('Bag weight must be positive kg with at most three decimal places, or unknown.')
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE');principal=setup.authorize(store,con,principal,'approve');setup.available(store,con)
        history=rows(con);prior=next((r for r in history if r['payload']['event_id']==p['event_id']),None)
        if prior:
            if prior['payload']!=p or prior['actor_id']!=principal.id:raise ValueError('Submission identifier conflict.')
            return {'status':'ALREADY_RECORDED','revision':p['event_id']}
        if p['expected_revision']!=(history[-1]['payload']['event_id'] if history else None):raise ValueError('Costing settings changed; refresh before saving.')
        value={'payload':p,'actor_id':principal.id,'received_at':utc_text(utc_now())}
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",(KIND,json.dumps(value,sort_keys=True),time.time()))
        IdentityService(store)._event(con,principal,'FARM_COSTING_POLICY','farming',p['event_id'],'RECORDED')
    return {'status':'RECORDED','revision':p['event_id']}


def overview(store,principal):
    with store._connect() as con:
        principal=setup.authorize(store,con,principal,'finance');history=rows(con)
        return {'policies':history,'revision':history[-1]['payload']['event_id'] if history else None,'can_configure':principal.role=='Owner'}


def report(store,principal,*,start,end,revision=None):
    from . import costing
    with store._connect() as con:
        setup.authorize(store,con,principal,'finance');history=rows(con)
        selected=next((r for r in history if r['payload']['event_id']==revision),None) if revision else (history[-1] if history else None)
    if not selected:raise ValueError('Save or select costing settings first; missing values may remain unknown.')
    p=selected['payload']
    result=costing.report(store,principal,start=start,end=end,currency=p['currency'],feed_minor_per_kg=p['feed_minor_per_kg'])
    result.update(policy_revision=p['event_id'],policy_recorded_at=selected['received_at'],eggs_per_crate=p['eggs_per_crate'],kg_per_bag=p['kg_per_bag'])
    basis=p.get('price_basis', 'UNKNOWN' if p['feed_minor_per_kg'] is None else 'UNCLASSIFIED')
    result.update(price_basis=basis,price_source=p.get('price_source',''))
    result['limitations'].append({'UNKNOWN':'Feed price is unknown.', 'PLANNING_ESTIMATE':'Feed price is an optional planning estimate; it is not an actual recorded expense.', 'RECORDED_PRICE':'Feed price is reported by the Owner with a source reference; applicability to consumed stock has not been independently verified.', 'UNCLASSIFIED':'This older setting has no recorded price basis. It must not be presented as a verified actual price.'}[basis])
    result['limitations'].append('Applying a selected price to consumption does not create or alter a recorded expense or payment.')
    return result
