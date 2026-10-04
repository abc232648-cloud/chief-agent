"""Human physical counts and Owner-approved, append-only stock adjustments."""
from decimal import Decimal
import hashlib
import json
import time
from identity.service import IdentityService
from operations.time_integrity import aware_utc, utc_now, utc_text
from . import setup, journal, journal_queries

KIND = 'farm_physical_count_v1'
PREFIX = {'birds':'birds', 'eggs':'eggs', 'kg':'feed'}


def rows(con):
    return [json.loads(r[0]) for r in con.execute('SELECT data_json FROM domain_records WHERE domain=? AND kind=? ORDER BY id',('farming',KIND))]


def stock(con, entity_id, unit, observed_at):
    digest=hashlib.sha256();total=Decimal(0);opening=False
    for row in con.execute("SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? AND json_extract(data_json,'$.payload.entity_id')=? ORDER BY id",(journal.KIND,entity_id)):
        encoded=row[0].encode();digest.update(str(len(encoded)).encode()+b':'+encoded)
    for row in con.execute("SELECT data_json FROM domain_records d WHERE d.domain='farming' AND d.kind=? AND json_extract(d.data_json,'$.payload.entity_id')=? AND "+journal_queries.ACTIVE,(journal.KIND,entity_id)):
        p=json.loads(row[0])['payload']
        if p.get('historical_before_opening'):continue
        if journal.TYPES[p['kind']][0]!=unit or aware_utc(p['observed_at'])>aware_utc(observed_at):continue
        total+=Decimal(p['quantity'])*journal.TYPES[p['kind']][1]
        opening|=p['kind'].endswith('_opening')
    return digest.hexdigest(), format(total,'f') if opening else None


def append(store, principal, payload):
    if not isinstance(payload,dict):raise ValueError('A physical count or decision is required.')
    p=dict(payload);operation=p.get('operation');journal.identifier(p.get('event_id'))
    if not isinstance(operation,str):raise ValueError('Choose a physical count operation.')
    if operation=='COUNT':
        if set(p) not in ({'event_id','operation','entity_id','unit','quantity','observed_at','reason'}, {'event_id','operation','entity_id','unit','quantity','observed_at','reason','conversion'}) or not isinstance(p['unit'],str) or p['unit'] not in PREFIX:
            raise ValueError('Supply a stock identity, unit, count, observation time and reason.')
        journal.identifier(p['entity_id']);p['reason']=journal.bounded_text(p['reason'],400)
        validated=journal.validate({'event_id':p['event_id'],'kind':PREFIX[p['unit']]+'_opening','entity_id':p['entity_id'],'location':'Physical count','quantity':p['quantity'],'basis':'MEASURED','observed_at':p['observed_at'],'notes':'','corrects':None,'reason':''})
        p['quantity']=validated['quantity'];p['observed_at']=validated['observed_at']
        if aware_utc(p['observed_at'])>utc_now():raise ValueError('A physical count cannot be future dated.')
    elif operation in {'APPROVE','REJECT'}:
        if set(p)!={'event_id','operation','reference','reason'}:raise ValueError('Decision requires a count reference and reason.')
        journal.identifier(p['reference']);p['reason']=journal.bounded_text(p['reason'],400)
    else:raise ValueError('Unknown physical count operation.')
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal=setup.authorize(store,con,principal,'count' if operation=='COUNT' else 'approve')
        setup.available(store,con);history=rows(con)
        prior=next((r for r in history if r['payload']['event_id']==p['event_id']),None)
        if prior:
            if prior['payload']!=p or prior['actor_id']!=principal.id:raise ValueError('Submission identifier conflict.')
            return {'status':'ALREADY_RECORDED','record':prior}
        record={'payload':p,'actor_id':principal.id,'received_at':utc_text(utc_now())}
        if operation=='COUNT':
            if 'conversion' in p:
                from . import units
                units.check(con, {'kind':PREFIX[p['unit']]+'_opening','quantity':p['quantity'],'conversion':p['conversion']})
            entity=setup.entities(con).get(p['entity_id'])
            if not entity or entity['entity_type']!=('FEED_STORE' if p['unit']=='kg' else 'FLOCK'):
                raise ValueError('Choose a registered flock or feed store matching the count unit.')
            if entity['opened_on'] and aware_utc(p['observed_at']).astimezone(setup.LAGOS).date().isoformat()<entity['opened_on']:
                raise ValueError('Count predates the opening date.')
            revision,balance=stock(con,p['entity_id'],p['unit'],p['observed_at'])
            record.update(stock_revision=revision,recorded_balance=balance,
                          difference=None if balance is None else format(Decimal(p['quantity'])-Decimal(balance),'f'))
        else:
            target=next((r for r in history if r['payload']['event_id']==p['reference'] and r['payload']['operation']=='COUNT'),None)
            if not target:raise ValueError('Physical count was not found.')
            if any(r['payload'].get('reference')==p['reference'] for r in history):raise ValueError('Count already decided.')
            if operation=='APPROVE':
                count=target['payload']
                if target['difference'] is None:raise ValueError('Opening stock is unknown. Record a supported opening snapshot, then submit a new count.')
                revision,_=stock(con,count['entity_id'],count['unit'],count['observed_at'])
                if revision!=target['stock_revision']:raise ValueError('Stock history changed; review and submit a fresh count before approval.')
                delta=Decimal(target['difference'])
                if delta:
                    entity=setup.entities(con)[count['entity_id']]
                    adjustment=journal.validate({'event_id':'count-adjustment-'+p['event_id'],'kind':PREFIX[count['unit']]+'_adjustment_'+('in' if delta>0 else 'out'),
                        'entity_id':count['entity_id'],'location':entity['name'],'quantity':format(abs(delta),'f'),'basis':'MEASURED','observed_at':count['observed_at'],
                        'notes':'Owner-approved physical count '+p['reference']+'; '+p['reason'],'corrects':None,'reason':''})
                    change={'version':1,'payload':adjustment,'actor_id':principal.id,'actor_name':con.execute('SELECT username FROM human_identities WHERE id=?',(principal.id,)).fetchone()[0],'session_id':principal.session_id,'received_at':record['received_at'],'evidence_status':'USER_REPORTED'}
                    con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',('farming',journal.KIND,json.dumps(change,sort_keys=True),time.time()))
                    record['adjustment_id']=adjustment['event_id']
        con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',('farming',KIND,json.dumps(record,sort_keys=True),time.time()))
        IdentityService(store)._event(con,principal,'FARM_PHYSICAL_COUNT_'+operation,'farming',p['event_id'],'RECORDED')
    return {'status':'RECORDED','record':record}


def overview(store,principal):
    with store._connect() as con:
        principal=setup.authorize(store,con,principal,'read');role=setup.role(con,principal)
        history=rows(con);items=[]
        for record in history:
            p=record['payload']
            if p['operation']!='COUNT' or (role=='WORKER' and record['actor_id']!=principal.id):continue
            decisions=[r for r in history if r['payload'].get('reference')==p['event_id']]
            revision,_=stock(con,p['entity_id'],p['unit'],p['observed_at'])
            items.append({**record,'state':decisions[-1]['payload']['operation'] if decisions else 'PENDING',
                          'stale':revision!=record['stock_revision'],'decisions':decisions})
        return {'items':list(reversed(items)),'can_approve':role=='OWNER',
                'notice':'Physical counts are human reports. Only an Owner decision changes recorded stock; no equipment action occurs.'}
