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


def records(con,kind):
    return [json.loads(r[0]) for r in con.execute("SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id",(kind,))]


def day(value):
    if not isinstance(value,str) or len(value)!=10 or date.fromisoformat(value).isoformat()!=value:
        raise ValueError('Use a valid calendar date.')
    return value


def details(value,payload):
    if not isinstance(value,dict) or set(value)!={'category','contact_id','due_on','items'}:
        raise ValueError('Supply category, contact, due date and line items.')
    if payload['kind'] not in {'SALE','EXPENSE_CLAIM','PURCHASE_REQUEST','OPENING_RECEIVABLE','OPENING_PAYABLE'}:
        raise ValueError('Transaction details belong to a sale, expense or purchase request.')
    if value['category'] not in CATEGORIES:raise ValueError('Choose a financial category.')
    if value['contact_id'] is not None:identifier(value['contact_id'])
    if value['due_on'] is not None:day(value['due_on'])
    if not isinstance(value['items'],list) or len(value['items'])>30:raise ValueError('Use at most30 line items.')
    for item in value['items']:
        if not isinstance(item,dict) or set(item)!={'description','amount_minor'}:raise ValueError('Invalid line item.')
        bounded_text(item['description'],120)
        if type(item['amount_minor']) is not int or not 0<item['amount_minor']<=999999999999:raise ValueError('Line amounts must be positive exact minor units.')
    if value['items'] and sum(i['amount_minor'] for i in value['items'])!=payload['amount_minor']:
        raise ValueError('Line item amounts must equal the transaction total.')
    return value


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
        # Receipts are bounded/scoped; never materialize all attachment bytes.
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
            p=r['payload'];at=aware_utc(p['observed_at']).astimezone(setup.LAGOS).date().isoformat()
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
                balances.append({'id':p['event_id'],'counterparty':p['counterparty'],'currency':p['currency'],'kind':p['kind'],'outstanding_minor':str(owed),'disputed':p['event_id'] in disputes,'due_on':due,'overdue':bool(due and owed and due<utc_now().astimezone(setup.LAGOS).date().isoformat())})
            if p['kind']=='PAYMENT_CLAIM':
                parent=active.get(p['reference'],{}).get('payload',{})
                if contact_id and parent.get('details',{}).get('contact_id')!=contact_id:continue
                if p['event_id'] in confirmed:t['received' if parent.get('kind') in books.RECEIVABLES else 'paid']+=p['amount_minor']
                else:t['unconfirmed']+=p['amount_minor']
            if p['kind']=='PURCHASE_REQUEST' and p['event_id'] not in decided:t['pending_approvals']+=1
        # Money strings preserve exact totals even beyond JavaScript's safe integer range.
        digest=hashlib.sha256()
        for row in con.execute("SELECT kind,json_remove(data_json,'$.image_base64') FROM domain_records WHERE domain='farming' AND kind IN (?,?,?) ORDER BY id",(books.KIND,CONTACTS,RECEIPTS)):
            encoded=json.dumps(tuple(row),ensure_ascii=False).encode()
            digest.update(str(len(encoded)).encode()+b':'+encoded)
        revision=digest.hexdigest()
        page=list(reversed(selected))[offset:offset+100]
        page_ids={r['payload']['event_id'] for r in page}
        attachments=[]
        if page_ids:
            sql="SELECT json_extract(data_json,'$.event_id'),json_extract(data_json,'$.reference'),json_extract(data_json,'$.sha256') FROM domain_records WHERE domain='farming' AND kind=? AND json_extract(data_json,'$.reference') IN ("+','.join('?' for _ in page_ids)+')'
            attachments=[dict(zip(('event_id','reference','sha256'),row)) for row in con.execute(sql,(RECEIPTS,*sorted(page_ids)))]
        return {'revision':revision,'categories':CATEGORIES,'contacts':contacts,'summaries':{c:{k:str(v) for k,v in t.items()} for c,t in totals.items()},'balances':balances if include_all_balances else [b for b in balances if b['id'] in page_ids],
                'records':page,'record_count':len(selected),'offset':offset,'receipts':[{k:r[k] for k in ('event_id','reference','sha256')} for r in attachments],
                'can_approve':can_approve,'notice':'Current non-voided records; confirmations as of now. Period cash totals use the reported payment date. Outstanding amounts relate to the selected sales, expenses and opening debts. Opening debts and attached invoices are not new sales or expenses. These are not bank balances or formal profit statements.'}
