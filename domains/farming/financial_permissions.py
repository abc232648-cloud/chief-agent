"""Owner-controlled restrictions on existing General Manager finance authority."""
import json
import time
from . import setup, scope
from .journal import identifier
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text

KIND='farm_financial_permissions_v1'
LABELS={'access':'View and access bookkeeping','record_debts':'Record sales, expenses and opening debts','flag_debts':'Flag debt disputes','report_payments':'Report payment claims'}
DEFAULTS={key:True for key in LABELS}


def rows(con):
    values=[json.loads(r[0]) for r in con.execute("SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id",(KIND,))]
    return scope.filter_records(con,values)


def effective(con,human_id):
    history=[r for r in rows(con) if r['payload']['human_id']==human_id]
    return dict(history[-1]['payload']['permissions']) if history else dict(DEFAULTS)


def _manager_in_selected_farm(con,human_id):
    target=con.execute('SELECT role,domains,enabled FROM human_identities WHERE id=?',(human_id,)).fetchone()
    if not target or not target['enabled'] or target['role']!='Manager' or not {'farming','*'} & set(json.loads(target['domains'])):
        return False
    if not scope.tenant_mode_connection(con):
        return True
    from domains import tenancy
    selected=scope.require_for_connection(con)
    return any(item['active'] and item['human_id']==human_id and item['farm_id']==selected.farm_id and item['role']=='GENERAL_MANAGER'
               for item in tenancy._replay(con).values())


def configure(store,principal,p,*,farm_id=None):
    if scope.current() is not None:return _configure(store,principal,p)
    with scope.bind(store,principal,farm_id):return _configure(store,principal,p)


def _configure(store,principal,p):
    if not isinstance(p,dict) or set(p)!={'event_id','human_id','expected_revision','permissions'}:
        raise ValueError('Supply exactly the financial permission fields.')
    identifier(p['event_id']);identifier(p['human_id'])
    if p['expected_revision'] is not None:identifier(p['expected_revision'])
    if not isinstance(p['permissions'],dict) or set(p['permissions'])!=set(LABELS) or any(type(v) is not bool for v in p['permissions'].values()):
        raise ValueError('Each financial permission must be explicitly on or off.')
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE');principal=setup.authorize(store,con,principal,'approve');setup.available(store,con)
        if not _manager_in_selected_farm(con,p['human_id']):
            raise PermissionError('Choose an enabled General Manager assigned to the selected Farm.')
        history=rows(con)
        prior=next((r for r in history if r['payload']['event_id']==p['event_id']),None)
        if prior:
            if prior['payload']!=p or prior['actor_id']!=principal.id:raise ValueError('Submission identifier conflict.')
            return {'status':'ALREADY_RECORDED'}
        own=[r for r in history if r['payload']['human_id']==p['human_id']]
        if p['expected_revision']!=(own[-1]['payload']['event_id'] if own else None):raise ValueError('Permissions changed; refresh before saving.')
        record=scope.stamp({'payload':p,'actor_id':principal.id,'received_at':utc_text(utc_now())})
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",(KIND,json.dumps(record,sort_keys=True),time.time()))
        IdentityService(store)._event(con,principal,'FARM_FINANCIAL_PERMISSIONS','farming',p['human_id'],'RECORDED')
    return {'status':'RECORDED'}


def overview(store,principal,*,farm_id=None):
    if scope.current() is not None:return _overview(store,principal)
    with scope.bind(store,principal,farm_id):return _overview(store,principal)


def _overview(store,principal):
    with store._connect() as con:
        principal=setup.authorize(store,con,principal,'read')
        if setup.role(con,principal)!='OWNER':raise PermissionError('Only the Farm Owner manages financial permissions.')
        history=rows(con);users=[]
        for u in con.execute("SELECT id,username,role,domains FROM human_identities WHERE role='Manager' AND enabled=1"):
            if not {'farming','*'} & set(json.loads(u['domains'])) or not _manager_in_selected_farm(con,u['id']):continue
            own=[r for r in history if r['payload']['human_id']==u['id']]
            users.append({'id':u['id'],'username':u['username'],'permissions':effective(con,u['id']),'revision':own[-1]['payload']['event_id'] if own else None})
        return {'users':users,'labels':LABELS,'notice':'Existing Manager access is retained until changed for this Farm. These switches restrict existing Farm management authority. Approvals, dispute resolution and payment confirmation remain Owner-only.'}
