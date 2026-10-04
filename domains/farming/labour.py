"""Reported temporary labour, with explicit links to existing expenses; no payroll."""
import json
import re
import time
from decimal import Decimal
from . import setup, bookkeeping as books, finance
from .journal import identifier, bounded_text
from identity.service import IdentityService
from operations.time_integrity import aware_utc, utc_now, utc_text

KIND='farm_labour_v1'
WORK={'event_id','operation','person','task','time_basis','work_date','started_at','ended_at','days','currency','agreed_total_minor','corrects','reason'}


def rows(con):
    result=con.execute("SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id LIMIT 10001",(KIND,)).fetchall()
    if len(result)>10000:raise ValueError('Labour history capacity requires indexed upgrade; no truncated totals returned.')
    return [json.loads(r[0]) for r in result]


def active_links(history):
    removed={r['payload']['link_id'] for r in history if r['payload']['operation']=='UNLINK_EXPENSE'}
    return [r['payload'] for r in history if r['payload']['operation']=='LINK_EXPENSE' and r['payload']['event_id'] not in removed]


def append(store,principal,payload):
    if not isinstance(payload,dict):raise ValueError('Supply a labour record.')
    p=dict(payload);identifier(p.get('event_id'));operation=p.get('operation')
    if operation=='WORK':
        if set(p)!=WORK:raise ValueError('Supply exactly the work and pay fields.')
        p['person']=bounded_text(p['person'],120);p['task']=bounded_text(p['task'],400);p['reason']=bounded_text(p['reason'],400)
        finance.day(p['work_date'])
        if p['work_date']>utc_now().astimezone(setup.LAGOS).date().isoformat():raise ValueError('Record actual work, not a future plan.')
        if p['time_basis']=='TIMED':
            if p['days'] is not None:raise ValueError('Timed work does not also supply days.')
            start,end=aware_utc(p['started_at']),aware_utc(p['ended_at'])
            if end<=start or end>utc_now() or (end-start).total_seconds()>7*86400:raise ValueError('Use a completed time range of at most seven days.')
            if start.astimezone(setup.LAGOS).date().isoformat()!=p['work_date']:raise ValueError('Work date must match the start date in Lagos.')
            p['started_at']=utc_text(start);p['ended_at']=utc_text(end)
        elif p['time_basis']=='DAYS':
            if p['started_at'] is not None or p['ended_at'] is not None:raise ValueError('Day-based work does not invent clock times.')
            if not isinstance(p['days'],str) or not re.fullmatch(r'\d{1,3}(?:\.\d{1,3})?',p['days']) or not 0<Decimal(p['days'])<=365:raise ValueError('Supply an explicit positive number of days, up to365.')
        else:raise ValueError('Choose recorded times or stated days.')
        if p['currency'] not in books.CURRENCIES:raise ValueError('Choose a supported currency.')
        total=p['agreed_total_minor']
        if total is not None and (type(total) is not int or not 0<=total<=999999999999):raise ValueError('Agreed total must be exact minor units or unknown.')
        if p['corrects'] is not None:identifier(p['corrects'])
    elif operation=='LINK_EXPENSE':
        if set(p)!={'event_id','operation','work_id','expense_id','reason'}:raise ValueError('Supply a work record, expense and link reason.')
        identifier(p['work_id']);identifier(p['expense_id']);p['reason']=bounded_text(p['reason'],400)
    elif operation=='UNLINK_EXPENSE':
        if set(p)!={'event_id','operation','link_id','reason'}:raise ValueError('Supply the exact expense link and reason for removing it.')
        identifier(p['link_id']);p['reason']=bounded_text(p['reason'],400)
    else:raise ValueError('Choose work entry or expense link.')
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE');principal=setup.authorize(store,con,principal,'approve' if operation in {'LINK_EXPENSE','UNLINK_EXPENSE'} else 'finance');setup.available(store,con)
        history=rows(con);prior=next((r for r in history if r['payload']['event_id']==p['event_id']),None)
        if prior:
            if prior['payload']!=p or prior['actor_id']!=principal.id:raise ValueError('Submission identifier conflict.')
            return {'status':'ALREADY_RECORDED'}
        replaced={r['payload']['corrects'] for r in history if r['payload']['operation']=='WORK'}
        active={r['payload']['event_id']:r['payload'] for r in history if r['payload']['operation']=='WORK' and r['payload']['event_id'] not in replaced}
        links=active_links(history)
        if operation=='UNLINK_EXPENSE' and not any(r['event_id']==p['link_id'] for r in links):raise ValueError('Expense link missing or already removed. Refresh before changing it.')
        if operation=='WORK' and p['corrects']:
            if p['corrects'] not in active:raise ValueError('Work record missing or already corrected.')
            if any(r['work_id']==p['corrects'] for r in links):raise ValueError('Linked work needs coordinated expense review before correction; original records retained.')
        if operation=='LINK_EXPENSE':
            work=active.get(p['work_id']);expenses={r['payload']['event_id']:r['payload'] for r in books.active(books.rows(con))}
            expense=expenses.get(p['expense_id'])
            if not work or not expense or expense['kind']!='EXPENSE_CLAIM' or expense.get('details',{}).get('category')!='WAGES':raise ValueError('Choose current work and an existing wages expense.')
            if work['agreed_total_minor'] is None or expense['amount_minor']!=work['agreed_total_minor'] or expense['currency']!=work['currency'] or expense['counterparty']!=work['person']:raise ValueError('Expense must match the known agreed total, currency and person/team.')
            if any(r['work_id']==p['work_id'] or r['expense_id']==p['expense_id'] for r in links):raise ValueError('Work or expense is already linked; no duplicate cost is created.')
        value={'payload':p,'actor_id':principal.id,'received_at':utc_text(utc_now())}
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",(KIND,json.dumps(value,sort_keys=True),time.time()))
        IdentityService(store)._event(con,principal,'FARM_LABOUR_'+operation,'farming',p['event_id'],'RECORDED')
    return {'status':'RECORDED'}


def overview(store,principal,offset=0):
    if type(offset)is not int or offset<0:raise ValueError('Invalid page.')
    with store._connect() as con:
        principal=setup.authorize(store,con,principal,'finance');history=rows(con)
        replaced={r['payload']['corrects'] for r in history if r['payload']['operation']=='WORK'}
        links={r['work_id']:r for r in active_links(history)}
        link_owners={r['payload']['event_id']:r['payload']['work_id'] for r in history if r['payload']['operation']=='LINK_EXPENSE'}
        changes={}
        for r in history:
            p=r['payload'];work_id=p.get('work_id') or link_owners.get(p.get('link_id'))
            if work_id:changes.setdefault(work_id,[]).append(r)
        active_expenses={r['payload']['event_id'] for r in books.active(books.rows(con)) if r['payload']['kind']=='EXPENSE_CLAIM'}
        items=[]
        for record in reversed(history):
            p=record['payload']
            if p['operation']!='WORK':continue
            link=links.get(p['event_id']);expense=link['expense_id'] if link else None
            link_history=changes.get(p['event_id'],[])
            items.append({**record,'is_current':p['event_id'] not in replaced,'expense_id':expense,'expense_link_id':link['event_id'] if link else None,'link_history':link_history,'expense_status':'LINKED' if expense in active_expenses else 'NEEDS_REVIEW' if expense else 'NOT_LINKED'})
        return {'items':items[offset:offset+100],'total':len(items),'next_offset':offset+100 if offset+100<len(items) else None,'can_link':principal.role=='Owner','notice':'Recorded work and agreed pay do not create an expense or confirm payment. Use linked bookkeeping for amounts owed and paid.'}
