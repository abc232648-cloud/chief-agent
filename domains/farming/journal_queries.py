"""Indexed journal queries. Whole-history data stays in SQLite, not a Python list."""
import json
from decimal import Decimal
from database.farm_indexes import WHERE, STOCK

ACTIVE = "NOT EXISTS (SELECT 1 FROM domain_records c WHERE c.domain='farming' AND c.kind='poultry_journal_v1' AND json_extract(c.data_json,'$.payload.corrects')=json_extract(d.data_json,'$.payload.event_id'))"


def prior(con, identity):
    row=con.execute('SELECT data_json FROM domain_records WHERE '+WHERE+" AND json_extract(data_json,'$.payload.event_id')=?",(identity,)).fetchone()
    return json.loads(row[0]) if row else None


def stock_rows(con, payload):
    from .journal import TYPES
    key='entity:'+payload['entity_id'] if 'entity_id' in payload else 'legacy:'+payload['location']
    kinds=tuple(k for k,v in TYPES.items() if v[0]==TYPES[payload['kind']][0])
    base='SELECT data_json FROM domain_records d WHERE '+WHERE+' AND '+STOCK+'=? AND '+ACTIVE+" AND json_extract(data_json,'$.payload.kind') IN ("+','.join('?' for _ in kinds)+')'
    args=(key,*kinds)
    result={}
    clauses=[(" AND json_extract(data_json,'$.payload.kind') LIKE '%_opening' LIMIT 1",()),
             (" AND coalesce(json_extract(data_json,'$.payload.historical_before_opening'),0)=0 ORDER BY json_extract(data_json,'$.payload.observed_at') LIMIT 1",())]
    if payload['corrects']:
        clauses.append((" AND json_extract(data_json,'$.payload.event_id')=? LIMIT 1",(payload['corrects'],)))
    for clause,extra in clauses:
        row=con.execute(base+clause,(*args,*extra)).fetchone()
        if row:
            record=json.loads(row[0]);result[record['payload']['event_id']]=record
    return list(result.values())


def overview(con, principal, manager, offset, limit):
    from .journal import TYPES, stock_key
    groups={}
    if manager:
        # Keep only a per-stock aggregate, not every record or correction ID.
        for row in con.execute('SELECT data_json FROM domain_records d WHERE '+WHERE+' AND '+ACTIVE+' ORDER BY id'):
            p=json.loads(row[0])['payload']
            if p.get('historical_before_opening'):continue
            unit,sign=TYPES[p['kind']]
            g=groups.setdefault((stock_key(p),unit),{'location':p['location'],'entity_id':p.get('entity_id'),'unit':unit,'total':Decimal(0),'opening_present':False,'contains_estimates':False,'last_observed_at':None})
            g['total']+=Decimal(p['quantity'])*sign
            g['opening_present']|=p['kind'].endswith('_opening')
            g['contains_estimates']|=p['basis']=='ESTIMATED'
            g['last_observed_at']=max(g['last_observed_at'] or '',p['observed_at'])
    from . import setup
    catalog=setup.entities(con)
    balances=[]
    for _,g in sorted(groups.items()):
        if g['entity_id'] in catalog:g['location']=catalog[g['entity_id']]['name']
        total=g.pop('total');g['recorded_balance']=format(total,'f') if g['opening_present'] else None
        g['needs_reconciliation']=total<0 if g['opening_present'] else True;balances.append(g)
    cte=''
    params=()
    visibility=''
    if not manager:
        # A worker sees their submissions and all later corrections to them.
        cte=("WITH RECURSIVE visible(event_id) AS (SELECT json_extract(data_json,'$.payload.event_id') FROM domain_records WHERE "+WHERE+
             " AND json_extract(data_json,'$.actor_id')=? UNION SELECT json_extract(c.data_json,'$.payload.event_id') FROM domain_records c JOIN visible v ON json_extract(c.data_json,'$.payload.corrects')=v.event_id WHERE c.domain='farming' AND c.kind='poultry_journal_v1') ")
        visibility=" AND json_extract(d.data_json,'$.payload.event_id') IN (SELECT event_id FROM visible)"
        params=(principal.id,)
    count=con.execute(cte+'SELECT count(*) FROM domain_records d WHERE '+WHERE+visibility,params).fetchone()[0]
    page=[{**json.loads(r[0]),'is_current':bool(r[1])} for r in con.execute(cte+'SELECT data_json,'+ACTIVE+' FROM domain_records d WHERE '+WHERE+visibility+' ORDER BY id DESC LIMIT ? OFFSET ?',(*params,limit,offset))]
    return {'balances':balances,'records':page,'record_count':count,'next_offset':offset+limit if offset+limit<count else None,
            'notice':'Recorded balances are not live measurements. Missing records and physical counts still need review.'}
