"""Private operational finance metadata and reports; no payment or accounting engine."""
import base64
import hashlib
import json
import re
import time
from datetime import date
from . import setup, bookkeeping as books, photos
from .journal import bounded_text, identifier
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text, aware_utc

CONTACTS='farm_finance_contact_v1'
RECEIPTS='farm_finance_receipt_v1'
CATEGORIES={'FEED':'Feed','BIRDS':'Birds','VETERINARY':'Veterinary care','WAGES':'Wages',
            'TRANSPORT':'Transport','POWER':'Power','MAINTENANCE':'Maintenance',
            'EGG_SALES':'Egg sales','BIRD_SALES':'Bird sales','OTHER':'Other'}
SALE_OUTPUTS={'EGG_SALES':'EGGS','BIRD_SALES':'MEAT'}


def records(con,kind):
    return [json.loads(r[0]) for r in con.execute("SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id",(kind,))]


def day(value):
    if not isinstance(value,str) or len(value)!=10 or date.fromisoformat(value).isoformat()!=value:
        raise ValueError('Use a valid calendar date.')
    return value


def details(value,payload):
    if not isinstance(value,dict):
        raise ValueError('Supply category, contact, due date and line items.')
    required = {'category','contact_id','due_on','items'}
    optional = {'flock_id','output_type','entity_id'}
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise ValueError('Supply category, contact, due date and line items.')
    if payload['kind'] not in {'SALE','EXPENSE_CLAIM','PURCHASE_REQUEST','OPENING_RECEIVABLE','OPENING_PAYABLE'}:
        raise ValueError('Transaction details belong to a sale, expense or purchase request.')
    if value['category'] not in CATEGORIES:raise ValueError('Choose a financial category.')
    if value['contact_id'] is not None:identifier(value['contact_id'])
    if value.get('flock_id') is not None:identifier(value['flock_id'])
    if value.get('entity_id') is not None:identifier(value['entity_id'])
    if value.get('output_type') is not None:bounded_text(value['output_type'], 40)
    if value['due_on'] is not None:day(value['due_on'])
    if not isinstance(value['items'],list) or len(value['items'])>30:raise ValueError('Use at most30 line items.')
    for item in value['items']:
        if not isinstance(item,dict) or set(item)!={'description','amount_minor'}:raise ValueError('Invalid line item.')
        bounded_text(item['description'],120)
        if type(item['amount_minor']) is not int or not 0<item['amount_minor']<=999999999999:raise ValueError('Line amounts must be positive exact minor units.')
    if value['items'] and sum(i['amount_minor'] for i in value['items'])!=payload['amount_minor']:
        raise ValueError('Line item amounts must equal the transaction total.')

    normalized=dict(value)
    provenance=[v for v in (value.get('flock_id'),value.get('entity_id')) if v is not None]
    if payload['kind']=='SALE' and value['category'] in SALE_OUTPUTS:
        if not provenance:
            raise ValueError('Egg and bird sales require the source flock.')
        if len(set(provenance))!=1:
            raise ValueError('Sale provenance must identify one source flock.')
        authoritative=SALE_OUTPUTS[value['category']]
        if value.get('output_type') not in (None,authoritative):
            raise ValueError('Sale output is determined by its category and cannot be overridden.')
        normalized['flock_id']=provenance[0]
        normalized.pop('entity_id',None)
        normalized['output_type']=authoritative
    elif provenance or value.get('output_type') is not None:
        raise ValueError('Flock/output provenance belongs to egg or bird sales.')
    return normalized


def validate_contact(con,p):
    value=p.get('details')
    if not value or not value['contact_id']:return
    contact=next((r for r in records(con,CONTACTS) if r['event_id']==value['contact_id']),None)
    required='CUSTOMER' if p['kind'] in books.RECEIVABLES else 'SUPPLIER'
    if not contact or contact['type'] not in {required,'BOTH'}:raise ValueError('Choose an existing contact with the appropriate customer/supplier role.')
    if p['counterparty']!=contact['name']:raise ValueError('Contact name differs from the selected contact.')


def append(store,principal,payload):
    if not isinstance(payload,dict):raise ValueError('A financial record is required.')
    operation=payload.get('operation')
    if operation=='contact':
        if set(payload) not in ({'operation','event_id','name','type','contact'},{'operation','event_id','name','type','contact','phone','email'}):raise ValueError('Supply exactly the contact fields.')
        if 'phone' in payload:
            phone=payload['phone'];email=payload['email']
            if not isinstance(phone,str) or len(phone)>40 or (phone and not re.fullmatch(r'[+0-9 ()-]{3,40}',phone)):
                raise ValueError('Use a phone number with digits and optional country-code formatting.')
            if not isinstance(email,str) or len(email)>254 or (email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email)):
                raise ValueError('Use a valid email address or leave it blank.')
        identifier(payload['event_id']);bounded_text(payload['name'],120);bounded_text(payload['contact'],160,empty=True)
        if payload['type'] not in {'CUSTOMER','SUPPLIER','BOTH'}:raise ValueError('Choose customer or supplier.')
        kind=CONTACTS;value=dict(payload)
    elif operation=='receipt':
        if set(payload)!={'operation','event_id','reference','image_base64'}:raise ValueError('Supply exactly the receipt fields.')
        identifier(payload['event_id']);identifier(payload['reference'])
        raw=photos.normalize_png(payload['image_base64'])
        kind=RECEIPTS;value={**payload,'image_base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest()}
    else:raise ValueError('Unknown financial operation.')
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE');principal=setup.authorize(store,con,principal,'finance');setup.available(store,con)
        existing=con.execute("SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? AND json_extract(data_json,'$.event_id')=?",(kind,value['event_id'])).fetchone()
        if existing:
            prior=json.loads(existing[0])
            if prior['actor_id']!=principal.id or any(prior.get(k)!=v for k,v in value.items()):raise ValueError('Submission identifier conflict.')
            return {'status':'ALREADY_RECORDED','id':value['event_id']}
        if operation=='contact':
            if any(r['name'].casefold()==value['name'].casefold() for r in records(con,CONTACTS)):raise ValueError('A contact with this name already exists. Use the existing contact.')
        else:
            current={r['payload']['event_id']:r['payload'] for r in books.active(books.rows(con))}
            if value['reference'] not in current:raise ValueError('Receipt needs an existing, non-voided financial record.')
            count=con.execute("SELECT count(*) FROM domain_records WHERE domain='farming' AND kind=? AND json_extract(data_json,'$.reference')=?",(kind,value['reference'])).fetchone()[0]
            if count>=5:raise ValueError('At most5 receipts per financial record.')
        value.update(actor_id=principal.id,received_at=utc_text(utc_now()))
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",(kind,json.dumps(value),time.time()))
        IdentityService(store)._event(con,principal,'FARM_FINANCE_'+operation.upper(),'farming',value['event_id'],'RECORDED')
    return {'status':'RECORDED','id':value['event_id']}


def receipt(store,principal,identity):
    identifier(identity)
    with store._connect() as con:
        con.execute('BEGIN')
        setup.authorize(store,con,principal,'finance')
        row=con.execute("SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? AND json_extract(data_json,'$.event_id')=?",(RECEIPTS,identity)).fetchone()
        if not row:raise FileNotFoundError('Receipt not found.')
        return base64.b64decode(json.loads(row[0])['image_base64'])


def linked_history(store,principal,identity):
    """Read a bounded financial reference chain, including voided originals."""
    identifier(identity)
    with store._connect() as con:
        con.execute('BEGIN')
        setup.authorize(store,con,principal,'finance')
        history=books.rows(con)
        by_id={r['payload']['event_id']:r for r in history}
        if identity not in by_id:raise FileNotFoundError('Financial record not found.')
        children={}
        for row in history:
            children.setdefault(row['payload']['reference'],[]).append(row['payload']['event_id'])
        pending=[identity];seen=set()
        while pending and len(seen)<1000:
            key=pending.pop()
            if key in seen or key not in by_id:continue
            seen.add(key);parent=by_id[key]['payload']['reference']
            if parent and parent not in seen:pending.append(parent)
            pending.extend(k for k in children.get(key,[]) if k not in seen)
        current={r['payload']['event_id'] for r in books.active(history)}
        records=[]
        for row in history:
            p=row['payload']
            if p['event_id'] not in seen:continue
            actor=con.execute('SELECT username FROM human_identities WHERE id=?',(row['actor_id'],)).fetchone()
            records.append({'payload':p,'actor':actor[0] if actor else 'Former account',
                            'received_at':row['received_at'],'is_current':p['event_id'] in current})
    return {'records':records,'truncated':any(k not in seen for k in pending),
            'notice':'Linked reported records and decisions, including voided originals. This history does not prove bank settlement.'}


def report(store,principal,*,start='',end='',query='',contact_id='',offset=0,include_all_balances=False):
    if start:day(start)
    if end:day(end)
    if start and end and start>end:raise ValueError('Start date must not follow end date.')
    bounded_text(query,120,empty=True)
    if contact_id:identifier(contact_id)
    if type(offset)is not int or not 0<=offset<=1000000:raise ValueError('Invalid page offset.')
    with store._connect() as con:
        con.execute('BEGIN')
        setup.authorize(store,con,principal,'finance')
        history=books.rows(con);contacts=records(con,CONTACTS)
        can_approve=setup.role(con,principal)=='OWNER'
        active={r['payload']['event_id']:r for r in books.active(history)}
        disputes=books.disputed({key:value['payload'] for key,value in active.items()})
        confirmed={r['payload']['reference'] for r in active.values() if r['payload']['kind']=='CONFIRM_PAYMENT'}
        def matches(r):
            p=r['payload'];at=aware_utc(p['observed_at']).astimezone(setup.farm_zone(con)).date().isoformat()
            contact=p.get('details',{}).get('contact_id')
            if p['kind'] in {'PAYMENT_CLAIM','DEBT_DOCUMENT'}:contact=active.get(p['reference'],{}).get('payload',{}).get('details',{}).get('contact_id')
            return (not start or at>=start) and (not end or at<=end) and (not query or query.casefold() in (p['counterparty']+' '+p['reason']+' '+p['receipt_ref']+' '+p['event_id']).casefold()) and (not contact_id or contact==contact_id)
        selected=[r for r in active.values() if matches(r)]
        decided={r['payload']['reference'] for r in active.values() if r['payload']['kind'] in {'APPROVE_REQUEST','REJECT_REQUEST'}}
        totals={};balances=[];payments={}
        for r in active.values():
            p=r['payload']
            if p['kind']=='PAYMENT_CLAIM':payments.setdefault(p['reference'],[]).append(p)
        for r in selected:
            p=r['payload'];t=totals.setdefault(p['currency'],{k:0 for k in ['sales','expenses','received','paid','receivable','payable','unconfirmed','pending_approvals']})
            if p['kind'] in books.OBLIGATIONS:
                incoming=p['kind'] in books.RECEIVABLES
                if p['kind'] in {'SALE','EXPENSE_CLAIM'}:t['sales' if incoming else 'expenses']+=p['amount_minor']
                paid=sum(v['amount_minor'] for v in payments.get(p['event_id'],[]) if v['event_id'] in confirmed)
                owed=p['amount_minor']-paid;t['receivable' if incoming else 'payable']+=owed
                due=p.get('details',{}).get('due_on')
                balances.append({'record':r,'outstanding_minor':owed,'paid_minor':paid,'due_on':due,'disputed':p['event_id'] in disputes})
            elif p['kind']=='PURCHASE_REQUEST' and p['event_id'] not in decided:t['pending_approvals']+=p['amount_minor']
            elif p['kind']=='CONFIRM_PAYMENT':
                claim=active.get(p['reference']);parent=active.get(claim['payload']['reference']) if claim else None
                if parent:t['received' if parent['payload']['kind'] in books.RECEIVABLES else 'paid']+=claim['payload']['amount_minor']
        all_balances=balances if include_all_balances else balances[:100]
        return {'records':list(reversed(selected[offset:offset+100])),'balances':all_balances,'totals':totals,'contacts':contacts,
                'can_approve':can_approve,'next_offset':offset+100 if offset+100<len(selected) else None,
                'notice':'Reported operational bookkeeping; no payment execution, bank confirmation or statutory accounting.'}
