from datetime import datetime, timezone
import math
import time
from domains.contracts import AgentDefinition, DomainAction, DomainDefinition


def text(value,label,limit=240):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        raise ValueError(f'{label} must contain 1–{limit} characters.')
    return value.strip()


def record_soil(storage,payload):
    if set(payload)!={'plot','sample_date','ph','source','confirmed'} or payload['confirmed'] is not True:
        raise ValueError('Confirm that you are entering an actual observed soil result; inferred results are not accepted.')
    plot=text(payload['plot'],'Plot');source=text(payload['source'],'Source')
    sampled=datetime.strptime(payload['sample_date'],'%Y-%m-%d').date()
    if sampled>datetime.now(timezone.utc).date():raise ValueError('A soil sample cannot be from the future.')
    if isinstance(payload['ph'],bool):raise ValueError('pH must be a number.')
    ph=float(payload['ph'])
    if not math.isfinite(ph) or not 0<=ph<=14:raise ValueError('Enter a pH value from 0 to 14.')
    data={'plot':plot,'sample_date':sampled.isoformat(),'ph':ph,'source':source,'evidence_status':'USER_RECORDED'}
    record_id=storage.record('soil_test',data)
    return {'status':'COMPLETED','record_id':record_id,'message':'Soil result recorded as user-supplied evidence, not independently verified.'}


def schedule_reminder(storage,payload):
    if set(payload)!={'title','due_at'}:raise ValueError('Supply a reminder title and due time only.')
    title=text(payload['title'],'Reminder')
    due=datetime.fromisoformat(payload['due_at'].replace('Z','+00:00'))
    if due.tzinfo is None:raise ValueError('Reminder time must include a timezone.')
    stamp=due.timestamp()
    if not time.time()<stamp<=time.time()+366*86400:raise ValueError('Choose a reminder time within the next year.')
    return {'status':'COMPLETED','reminder_id':storage.remind(title,stamp),'message':'Reminder scheduled for the dashboard inbox.'}


def cancel_reminder(storage,payload):
    if set(payload)!={'reminder_id'}:raise ValueError('Supply the reminder ID only.')
    reminder_id=payload['reminder_id']
    if isinstance(reminder_id,bool) or not isinstance(reminder_id,int) or reminder_id<1:
        raise ValueError('Reminder ID must be a positive integer.')
    storage.cancel_reminder(reminder_id)
    return {'status':'COMPLETED','message':'Reminder cancelled.'}


def definition():
    actions=(
        DomainAction('record_soil_test','Record soil-test result','farming.records.write',(
            {'name':'plot','label':'Plot or field','type':'text'},
            {'name':'sample_date','label':'Sample date','type':'date'},
            {'name':'ph','label':'Measured pH','type':'number','min':0,'max':14,'step':'any'},
            {'name':'source','label':'Source (lab report or observation)','type':'text'},
            {'name':'confirmed','label':'I am entering an actual measured result','type':'checkbox'}),record_soil),
        DomainAction('schedule_reminder','Schedule reminder','farming.reminders.write',(
            {'name':'title','label':'Reminder','type':'text'},
            {'name':'due_at','label':'Due at (your browser’s local time)','type':'datetime-local'}),schedule_reminder),
        DomainAction('cancel_reminder','Cancel reminder','farming.reminders.write',(
            {'name':'reminder_id','label':'Reminder ID','type':'number','min':1,'step':1},),cancel_reminder))
    return (DomainDefinition('farming','Farm Agent','Farm records and reminders, with an isolated poultry-record pilot. Equipment automation is not enabled.',actions, pages=(('farmRecords','Poultry records'),)),
            AgentDefinition('farming-recorder','farming',frozenset({'farming.records.read','farming.records.write','farming.reminders.write'})))
