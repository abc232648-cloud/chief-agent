"""Human-reported flock health history. No diagnosis, prescribing or treatment action."""
import json
import re
import time
from decimal import Decimal
from . import setup,finance
from .journal import identifier,bounded_text
from operations.time_integrity import utc_now,utc_text,aware_utc
from identity.service import IdentityService

KIND='farm_health_v1'
KINDS={'OBSERVATION','VACCINATION','MEDICINE','VET_VISIT','LAB_RESULT'}
FIELDS={'event_id','kind','entity_id','observed_at','summary','product','quantity','unit','administered_by','batch','expires_on','source_reference','instructions','instruction_source','corrects','reason'}


def rows(con):
    values=con.execute("SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id LIMIT 10001",(KIND,)).fetchall()
    if len(values)>10000:raise ValueError('Health history requires indexed upgrade; partial history is not returned.')
    return [json.loads(r[0]) for r in values]


def append(store,principal,payload):
    if not isinstance(payload,dict) or set(payload)!=FIELDS:raise ValueError('Supply exactly the health record fields.')
    p=dict(payload);identifier(p['event_id']);identifier(p['entity_id'])
    if not isinstance(p['kind'],str) or p['kind'] not in KINDS:raise ValueError('Choose a health record type.')
    observed=aware_utc(p['observed_at'])
    if observed>utc_now():raise ValueError('Record actual observations or administration, not future treatment plans.')
    p['observed_at']=utc_text(observed)
    for key,limit in [('summary',2000),('product',160),('unit',40),('administered_by',120),('batch',120),('source_reference',400),('instructions',2000),('instruction_source',400),('reason',400)]:p[key]=bounded_text(p[key],limit,empty=key!='summary')
    if p['quantity'] is not None:
        if not isinstance(p['quantity'],str) or not re.fullmatch(r'\d{1,9}(?:\.\d{1,3})?',p['quantity']) or Decimal(p['quantity'])<=0 or not p['unit']:raise ValueError('Recorded quantity needs a positive exact number and explicit unit.')
    elif p['unit']:raise ValueError('Leave unit blank when the administered quantity is unknown.')
    if p['kind'] in {'VACCINATION','MEDICINE'}:
        if not p['product'] or not p['administered_by']:raise ValueError('Record the product and person who administered it.')
    elif any(p[k] not in ('',None) for k in ('product','quantity','unit','administered_by','batch','expires_on')):raise ValueError('Product administration fields belong to vaccination or medicine records.')
    if p['expires_on'] is not None:finance.day(p['expires_on'])
    if p['instructions'] and not p['instruction_source']:raise ValueError('Instructions require their vet/document source; Chief does not generate them.')
    if p['corrects'] is not None:
        identifier(p['corrects'])
        if not p['reason']:raise ValueError('Explain the correction.')
    elif p['reason']:raise ValueError('Correction reason needs an original record.')
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE');principal=setup.authorize(store,con,principal,'manage' if p['corrects'] else 'report');setup.available(store,con)
        history=rows(con);prior=next((r for r in history if r['payload']['event_id']==p['event_id']),None)
        if prior:
            if prior['payload']!=p or prior['actor_id']!=principal.id:raise ValueError('Submission identifier conflict.')
            return {'status':'ALREADY_RECORDED'}
        entity=setup.entities(con).get(p['entity_id'])
        if not entity or entity['entity_type']!='FLOCK':raise ValueError('Choose a registered flock.')
        if entity['opened_on'] and observed.astimezone(setup.LAGOS).date().isoformat()<entity['opened_on']:raise ValueError('Health record predates the flock opening date.')
        if p['corrects']:
            replaced={r['payload']['corrects'] for r in history}
            original=next((r['payload'] for r in history if r['payload']['event_id']==p['corrects'] and p['corrects'] not in replaced),None)
            if not original:raise ValueError('Record missing or already corrected.')
            if any(p[k]!=original[k] for k in ('kind','entity_id','observed_at')):raise ValueError('Corrections retain flock, record type and observation time.')
        record={'payload':p,'actor_id':principal.id,'received_at':utc_text(utc_now()),'evidence_status':'HUMAN_REPORTED'}
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",(KIND,json.dumps(record,sort_keys=True),time.time()))
        IdentityService(store)._event(con,principal,'FARM_HEALTH_RECORDED','farming',p['event_id'],'RECORDED')
    return {'status':'RECORDED'}


def overview(store,principal,offset=0):
    if type(offset)is not int or offset<0:raise ValueError('Invalid history page.')
    with store._connect() as con:
        principal=setup.authorize(store,con,principal,'read');manager=setup.role(con,principal) in {'OWNER','GENERAL_MANAGER','SUPERVISOR'}
        history=rows(con);replaced={r['payload']['corrects'] for r in history};visible=[];related=set()
        for record in history:
            p=record['payload']
            if manager or record['actor_id']==principal.id or p['corrects'] in related:
                related.add(p['event_id']);visible.append({**record,'is_current':p['event_id'] not in replaced,'expired_at_administration':bool(p['expires_on'] and p['expires_on']<aware_utc(p['observed_at']).astimezone(setup.LAGOS).date().isoformat())})
        visible.reverse()
        return {'items':visible[offset:offset+100],'total':len(visible),'next_offset':offset+100 if offset+100<len(visible) else None,'can_correct':manager,'notice':'Reported history only. Recording a treatment does not recommend it, authorize it, or establish a withdrawal period. Unknown details remain unknown.'}
