"""One manual aggregate preview. No scheduler, external delivery or Job reads."""
import hashlib
import http.client
import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

from . import n8n, n8n_startup
from capabilities.contracts import Node, Mode
from database.component_state import ComponentState
from decision_ledger.service import DecisionLedger
from evidence.service import canonical
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text
from policy.contracts import ActionRisk, Outcome, PolicyPack, PolicyRule, PolicyRequest, Precedence
from policy.engine import PolicyEngine
from security.permissions import worker_context

CONTRACT='chief.internal-report.v1'
CAPABILITY='chief.internal_report_preview'
SERVICE='chief.internal_reporting'
PATH='/webhook/chief-internal-report-v1'
PREFIX='integration.n8n.report.'
ACTION='internal-report.preview'
NOTICE='Counts describe recorded journal entries and corrections, not production, stock or a complete day. Missing activity remains unknown.'


def _policy():
    return PolicyEngine((PolicyPack('chief.internal-report','1.0.0',Precedence.CHIEF,
        (PolicyRule(ACTION,Outcome.ALLOW,'Confirmed local aggregate preview only'),)),)).evaluate(
            PolicyRequest(ACTION,ActionRisk.RECORD))


def _configuration():
    # The startup gate is mandatory, so its private configuration is required too.
    n8n_startup.n8n_handoff._configuration()
    base=n8n.configuration()
    workflow=os.getenv('CHIEF_N8N_REPORT_WORKFLOW_ID','')
    api=os.getenv('CHIEF_N8N_REPORT_API_KEY','')
    key=os.getenv('CHIEF_N8N_REPORT_KEY','')
    if base is None or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',workflow):
        raise ValueError('Configure the reviewed local report workflow privately.')
    for value in (api,key):
        if not 32<=len(value)<=4096 or any(ord(c)<33 or ord(c)>126 for c in value):
            raise ValueError('Separate private reporting credentials are required.')
    if len({api,key,base[1],os.getenv('CHIEF_N8N_HANDSHAKE_KEY','')})!=4:
        raise ValueError('Reporting, inventory and handshake credentials must be separate.')
    return base[0],workflow,api,key


def configured():
    try:_configuration();return True
    except ValueError:return False


def _http(parsed,path,*,key_name,key,body=None):
    cls=http.client.HTTPSConnection if parsed.scheme=='https' else http.client.HTTPConnection
    connection=cls('127.0.0.1',parsed.port,timeout=5)
    deadline=time.monotonic()+5
    try:
        connection.request('POST' if body is not None else 'GET',path,
            body=canonical(body).encode() if body is not None else None,
            headers={key_name:key,'Content-Type':'application/json','Accept':'application/json'})
        response=connection.getresponse()
        if response.status!=200 or response.getheader('Content-Type','').split(';')[0].strip()!='application/json':
            raise ValueError('Workflow response could not be verified.')
        limit=8192 if body is not None else 131072
        chunks=[];size=0
        while size<=limit:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ValueError('Workflow response exceeded its deadline.')
            if connection.sock is not None:connection.sock.settimeout(remaining)
            chunk=response.read1(limit+1-size)
            if not chunk:break
            chunks.append(chunk);size+=len(chunk)
        raw=b''.join(chunks)
        if time.monotonic()>=deadline:raise ValueError('Workflow response exceeded its deadline.')
        if len(raw)>limit:raise ValueError('Workflow response exceeds its bound.')
        return json.loads(raw)
    except (OSError,http.client.HTTPException,UnicodeError,json.JSONDecodeError):
        raise ValueError('Local reporting service unavailable; no verified response.') from None
    finally:connection.close()


def _binding(config):
    parsed,workflow,api,_=config
    actual=_http(parsed,'/api/v1/workflows/'+workflow,key_name='X-N8N-API-KEY',key=api)
    expected=json.loads((Path(__file__).parent/'workflows/chief-internal-report-v1.json').read_text())
    if (not isinstance(actual,dict) or actual.get('active') is not True or actual.get('pinData') or actual.get('staticData')
            or actual.get('id')!=workflow or not actual.get('versionId') or actual.get('versionId')!=actual.get('activeVersionId')):
        raise ValueError('The reviewed report workflow must be active without pinned data.')
    nodes=actual.get('nodes')
    if not isinstance(nodes,list):raise ValueError('Invalid workflow binding.')
    nodes=json.loads(json.dumps(nodes))
    for node in nodes:
        if not isinstance(node,dict):raise ValueError('Invalid workflow node.')
        credentials=node.pop('credentials',None)
        if node.get('id')=='report-webhook':
            generated_id=node.pop('webhookId',None)
            if generated_id is not None and (not isinstance(generated_id,str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}',generated_id)):
                raise ValueError('Invalid generated webhook identity.')
            if (not isinstance(credentials,dict) or set(credentials)!={'httpHeaderAuth'}
                    or not isinstance(credentials['httpHeaderAuth'],dict) or not credentials['httpHeaderAuth'].get('id')):
                raise ValueError('The report workflow requires its separate header credential.')
        elif credentials:
            raise ValueError('Unexpected workflow credentials.')
    pinned={k:expected[k] for k in ('nodes','connections','settings')}
    observed={'nodes':nodes,'connections':actual.get('connections'),'settings':actual.get('settings')}
    if observed!=pinned:raise ValueError('Report workflow changed; review its version before use.')
    return hashlib.sha256(canonical(pinned).encode()).hexdigest()


def _guard(store,con,principal,registry,*,catalog,sensitive=True):
    if principal is None:raise PermissionError('Sign in to request a report.')
    service=IdentityService(store)
    current=service._principal(con,principal.session_id)
    if current.id!=principal.id or current.role not in {'Owner','Administrator'} or '*' not in current.domains:
        raise PermissionError('Installation Owner or Administrator authority is required.')
    service.authorize(current,'installation.manage',sensitive=sensitive)
    row=con.execute('SELECT enabled,running FROM agent_controls WHERE domain=?',('farming',)).fetchone()
    enabled=con.execute('SELECT value FROM control_state WHERE key=?',(n8n.KEY,)).fetchone()
    if 'farming' not in registry.domains or not row or not all(row) or not enabled or enabled[0]!='true':
        raise PermissionError('Enable the Farm domain and local connection first.')
    nodes={Node('component','chief'),Node('component',SERVICE),Node('component','farming'),Node('component','farming-recorder'),Node('capability',CAPABILITY)}
    for node in tuple(nodes):nodes.update(catalog.graph.dependencies(node))
    state=ComponentState(store)
    if any(state.mode(con,n)!=Mode.ENABLED for n in nodes):
        raise PermissionError('Component or capability control prevents reporting.')
    result=_policy()
    if result.decision!=Outcome.ALLOW:raise PermissionError('Chief Policy requires review or blocks this report.')
    return current


def _ledger(store,con,row,phase,outcome,audit_id):
    agent=SimpleNamespace(id=SERVICE,domain='farming',capabilities=frozenset({CAPABILITY}))
    with worker_context(agent):
        def validate(ref):
            return ref=={'domain':'farming','kind':'audit_log','id':str(audit_id)} and bool(con.execute(
                'SELECT 1 FROM audit_log WHERE id=? AND actor=?',(audit_id,row['actor'])).fetchone())
        DecisionLedger(store,'farming',CAPABILITY,reference_validator=validate).append(
            event_key='report-'+row['operation_id']+'-'+phase.lower(),correlation_id=row['operation_id'],
            action=ACTION,phase=phase,outcome=outcome,rationale='HUMAN_CONFIRMED_AGGREGATE_PREVIEW',
            risk=ActionRisk.RECORD,policy_decision=_policy().decision.value,approval='APPROVED',
            policy_ref='chief.internal-report:1.0.0',connection=con,
            references=({'domain':'farming','kind':'audit_log','id':str(audit_id)},))


def _visible(row):
    result={k:row[k] for k in ('operation_id','domain','status','created_at')}
    if row['status']=='DISPATCHING' and time.time()>=row['deadline']:result['status']='UNKNOWN'
    if result['status']=='COMPLETED':result.update(snapshot=row['snapshot'],notice=NOTICE)
    else:result['notice']='No published report. An uncertain request is never resent automatically.'
    return result


def history(store,registry,principal,*,catalog):
    with store._connect() as con:
        _guard(store,con,principal,registry,catalog=catalog,sensitive=False)
        rows=con.execute('SELECT value FROM control_state WHERE key LIKE ? ORDER BY key',(PREFIX+'%',)).fetchall()
    return [_visible(r) for r in sorted((json.loads(r[0]) for r in rows),key=lambda r:r['created_at'],reverse=True)[:50]]


def run(store,registry,principal,body,*,catalog,snapshot_provider):
    if (not isinstance(body,dict) or set(body)!={'operation_id','domain','confirmed'} or body['confirmed'] is not True
            or body['domain']!='farming' or not isinstance(body['operation_id'],str) or not re.fullmatch('[a-f0-9]{32}',body['operation_id'])):
        raise ValueError('Confirm a Farm record-count preview with a unique request ID.')
    key=PREFIX+body['operation_id']
    def prior(con):
        old=con.execute('SELECT value FROM control_state WHERE key=?',(key,)).fetchone()
        if old:
            row=json.loads(old[0])
            if row['actor']!=principal.id:raise PermissionError('Request belongs to another operator.')
            return row
    with store._connect() as con:
        _guard(store,con,principal,registry,catalog=catalog)
        old=prior(con)
        if old:return _visible(old)
    config=_configuration()
    binding=_binding(config)  # Read-only, before any report payload is sent.
    if n8n_startup.qualify(confirmed=True)['status']!='STARTUP_READY':
        raise ValueError('Workflow runner is not ready. No report was dispatched.')
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        current=_guard(store,con,principal,registry,catalog=catalog)
        old=prior(con)
        if old:return _visible(old)
        if con.execute('SELECT count(*) FROM control_state WHERE key LIKE ?',(PREFIX+'%',)).fetchone()[0]>=500:
            raise ValueError('Report history limit reached; review required.')
        data=snapshot_provider(con)
        source_reference={'domain':'farming','kind':'poultry_journal_v1','last_record_id':data.pop('_source_cutoff_id')}
        row={'operation_id':body['operation_id'],'domain':'farming','actor':current.id,
             'created_at':utc_text(utc_now()),'deadline':time.time()+15,'status':'DISPATCHING',
             'snapshot':data,'snapshot_sha256':hashlib.sha256(canonical(data).encode()).hexdigest(),
             'workflow_sha256':binding,'contract':CONTRACT,'source_reference':source_reference}
        con.execute('INSERT INTO control_state VALUES(?,?)',(key,canonical(row)))
        audit_id=_audit(con,row,'INTENT')
        _ledger(store,con,row,'INTENT','DISPATCHING',audit_id)
    # Intent, human audit provenance and ledger commit together before I/O.
    payload={k:row[k] for k in ('operation_id','domain','deadline','snapshot','snapshot_sha256','contract')}
    payload['purpose']='internal-report-preview-only'
    expected={k:payload[k] for k in ('operation_id','domain','snapshot','snapshot_sha256','contract')}
    expected['status']='COMPLETED'
    started=time.monotonic()
    try:
        response=_http(config[0],PATH,key_name='X-Chief-Report-Key',key=config[3],body=payload)
        verified=response==expected and time.monotonic()-started<5 and time.time()<row['deadline']
    except ValueError:verified=False
    row['status']='COMPLETED' if verified else 'UNKNOWN'
    # Permission or policy changes can suppress publication, never authorize a resend.
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        try:_guard(store,con,principal,registry,catalog=catalog)
        except (PermissionError,ValueError):row['status']='WITHHELD'
        con.execute('UPDATE control_state SET value=? WHERE key=?',(canonical(row),key))
        audit_id=_audit(con,row,row['status'])
        _ledger(store,con,row,'OUTCOME',row['status'],audit_id)
    return _visible(row)


def _audit(con,row,status):
    return con.execute('INSERT INTO audit_log(category,actor,action,status,data_json) VALUES(?,?,?,?,?)',
        ('integration',row['actor'],ACTION,status,canonical({'operation_id':row['operation_id'],
         'domain':'farming','contract':CONTRACT,'snapshot_sha256':row['snapshot_sha256'],
         'workflow_sha256':row['workflow_sha256'],'source_reference':row['source_reference']}))).lastrowid
