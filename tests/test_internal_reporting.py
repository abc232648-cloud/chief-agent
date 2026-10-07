import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import pytest

from integrations import internal_reporting as report
from application.composition import default_registry
from identity.service import IdentityService
from operations.time_integrity import utc_now,utc_text
from policy.contracts import Outcome
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_identity_http import request

PATH='/api/integrations/n8n/report-preview'


def body():return {'operation_id':uuid.uuid4().hex,'domain':'farming','confirmed':True}


@pytest.fixture
def transport(dashboard,monkeypatch):
    monkeypatch.setenv('CHIEF_N8N_URL','http://127.0.0.1:5681')
    for name,value in [('API_KEY','inventory-test'),('HANDSHAKE_KEY','h'*40),('REPORT_API_KEY','a'*40),('REPORT_KEY','r'*40),('REPORT_WORKFLOW_ID','fixture')]:
        monkeypatch.setenv('CHIEF_N8N_'+name,value)
    with dashboard.store._connect() as con:con.execute('INSERT OR REPLACE INTO control_state VALUES(?,?)',(report.n8n.KEY,'true'))
    workflow=json.loads((Path(report.__file__).parent/'workflows/chief-internal-report-v1.json').read_text())
    workflow.update(active=True,id='fixture',versionId='v1',activeVersionId='v1')
    workflow['nodes'][0]['credentials']={'httpHeaderAuth':{'id':'header-credential'}}
    state={'calls':[],'workflow':workflow,'mode':'ok'}
    def http(parsed,path,*,key_name,key,body=None):
        if body is None:return state['workflow']
        state['calls'].append(body)
        if state['mode']=='lost':raise ValueError('No verified response')
        if callable(state['mode']):state['mode']()
        result={k:body[k] for k in ('operation_id','domain','snapshot','snapshot_sha256','contract')}
        result['status']='COMPLETED'
        if state['mode']=='wrong':result['operation_id']='0'*32
        return result
    monkeypatch.setattr(report,'_http',http)
    monkeypatch.setattr(report.n8n_startup,'qualify',lambda **_: {'status':'STARTUP_READY'})
    return state


def run(d,b):return d.app.CONTROL_SERVICES.reporting.run(d.store,default_registry(),d.credentials['principal'],b)


def test_snapshot_privacy_corrections_and_atomic_provenance(dashboard,transport):
    from domains.farming import journal
    from tests.test_farm_journal import record
    d=dashboard;p=record(notes='PRIVATE FARM NOTE',location='PRIVATE LOCATION')
    journal.append(d.store,d.credentials['principal'],p)
    journal.append(d.store,d.credentials['principal'],record(location=p['location'],corrects=p['event_id'],reason='PRIVATE REASON'))
    with d.store._connect() as con:
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('jobs','poultry_journal_v1',?,0)",(json.dumps({'secret':'PRIVATE JOB DATA'}),))
        schema=list(map(tuple,con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))
    b=body();result=run(d,b)
    assert result['status']=='COMPLETED'
    assert result['snapshot']['counts']=={'journal_entries':2,'current_entries':1,'correction_entries':1}
    assert 'PRIVATE' not in json.dumps(transport['calls'])
    assert '_source_cutoff_id' not in json.dumps(transport['calls'])
    assert 'source_reference' not in json.dumps(transport['calls'])
    # Later records must not change the stored preview when its ID is retried.
    journal.append(d.store,d.credentials['principal'],record())
    assert run(d,b)==result and len(transport['calls'])==1
    with d.store._connect() as con:
        assert schema==list(map(tuple,con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))
        ledger=[json.loads(r[0]) for r in con.execute('SELECT record FROM decision_ledger WHERE correlation_id=?',(b['operation_id'],))]
        assert [r['phase'] for r in ledger]==['INTENT','OUTCOME']
        for entry in ledger:
            row=con.execute('SELECT actor,data_json FROM audit_log WHERE id=?',(entry['references'][0]['id'],)).fetchone()
            assert row['actor']==d.credentials['principal'].id
            assert json.loads(row['data_json'])['operation_id']==b['operation_id']
            assert json.loads(row['data_json'])['source_reference']=={'domain':'farming','kind':'poultry_journal_v1','last_record_id':2}
        assert 'PRIVATE' not in str(ledger)


@pytest.mark.parametrize('role,domains,expected',[('Administrator',('*',),200),('Manager',('farming',),403),('Worker',('farming',),403),('Owner',('jobs',),403)])
def test_role_and_domain_matrix(dashboard,transport,role,domains,expected):
    d=dashboard;s=IdentityService(d.store)
    s.create_user(d.credentials['principal'],'report-human',PASSWORD,role,domains)
    raw,_=s.login('report-human',PASSWORD)
    assert request(d,PATH,'POST',body(),raw)[0]==expected
    assert len(transport['calls'])==(1 if expected==200 else 0)


@pytest.mark.parametrize('gate',['anonymous','csrf','host','origin','reauth','revoked','paused','disabled','capability','policy','startup'])
def test_denied_without_report_dispatch(dashboard,transport,monkeypatch,gate):
    from capabilities.contracts import Node,Mode
    d=dashboard;raw=d.credentials['raw'];options={}
    if gate=='anonymous':raw=None
    if gate=='csrf':options['csrf']=False
    if gate=='host':options['extra']={'Host':'invalid.example'}
    if gate=='origin':options['extra']={'Origin':'https://invalid.example'}
    with d.store._connect() as con:
        if gate=='reauth':con.execute('UPDATE human_sessions SET reauth_at=?',(utc_text(utc_now()-timedelta(minutes=6)),))
        if gate=='revoked':con.execute('UPDATE human_sessions SET revoked=1')
        if gate=='paused':con.execute("UPDATE agent_controls SET running=0 WHERE domain='farming'")
        if gate=='disabled':con.execute('UPDATE control_state SET value=? WHERE key=?',('false',report.n8n.KEY))
        if gate=='capability':report.ComponentState(d.store).put(con,Node('capability',report.CAPABILITY),Mode.DISABLED,actor='fixture',reason='test')
    if gate=='policy':monkeypatch.setattr(report,'_policy',lambda:SimpleNamespace(decision=Outcome.ASK))
    if gate=='startup':monkeypatch.setattr(report.n8n_startup,'qualify',lambda **_: {'status':'NOT_READY'})
    assert request(d,PATH,'POST',body(),raw,**options)[0] in {400,401,403,428}
    assert transport['calls']==[]


@pytest.mark.parametrize('change',['inactive','draft','node','connections','settings','pinned','credential'])
def test_changed_binding_denied(dashboard,transport,change):
    w=transport['workflow']
    if change=='inactive':w['active']=False
    if change=='draft':w['activeVersionId']='older'
    if change=='node':w['nodes'][1]['parameters']['jsCode']='return [];'
    if change=='connections':w['connections']={}
    if change=='settings':w['settings']['errorWorkflow']='unexpected'
    if change=='pinned':w['pinData']={'unsafe':[]}
    if change=='credential':w['nodes'][0]['credentials']={}
    with pytest.raises(ValueError):run(dashboard,body())
    assert transport['calls']==[]


@pytest.mark.parametrize('mode',['lost','wrong'])
def test_unknown_never_resent(dashboard,transport,mode):
    transport['mode']=mode;b=body()
    assert run(dashboard,b)['status']=='UNKNOWN'
    assert run(dashboard,b)['status']=='UNKNOWN'
    assert len(transport['calls'])==1


@pytest.mark.parametrize('change',['revoke','pause','policy'])
def test_revalidate_before_publication(dashboard,transport,monkeypatch,change):
    d=dashboard
    def change_authority():
        with d.store._connect() as con:
            if change=='revoke':con.execute('UPDATE human_sessions SET revoked=1')
            if change=='pause':con.execute("UPDATE agent_controls SET running=0 WHERE domain='farming'")
        if change=='policy':monkeypatch.setattr(report,'_policy',lambda:SimpleNamespace(decision=Outcome.BLOCK))
    transport['mode']=change_authority
    result=run(d,body())
    assert result['status']=='WITHHELD' and 'snapshot' not in result
    with d.store._connect() as con:
        entry=json.loads(con.execute("SELECT record FROM decision_ledger WHERE event_key LIKE '%-outcome'").fetchone()[0])
        assert entry['outcome']=='WITHHELD'
        if change=='policy':assert entry['policy_decision']=='BLOCK'


def test_concurrent_duplicate_is_single_dispatch(dashboard,transport):
    entered=Event();release=Event()
    def wait():entered.set();assert release.wait(10)
    transport['mode']=wait;b=body()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(run,dashboard,b);assert entered.wait(5)
        assert run(dashboard,b)['status']=='DISPATCHING'
        release.set();assert first.result(timeout=10)['status']=='COMPLETED'
    assert len(transport['calls'])==1


def test_ledger_failure_rolls_back_intent_without_send(dashboard,transport,monkeypatch):
    def fail(*a,**k):raise RuntimeError('Synthetic persistence failure')
    monkeypatch.setattr(report,'_ledger',fail)
    with pytest.raises(RuntimeError):run(dashboard,body())
    assert transport['calls']==[]
    with dashboard.store._connect() as con:
        assert not con.execute('SELECT 1 FROM control_state WHERE key LIKE ?',(report.PREFIX+'%',)).fetchone()
        assert not con.execute('SELECT 1 FROM audit_log WHERE action=?',(report.ACTION,)).fetchone()


def test_crash_after_dispatch_does_not_repeat(dashboard,transport):
    def crash():raise SystemExit('Synthetic crash')
    transport['mode']=crash;b=body()
    with pytest.raises(SystemExit):run(dashboard,b)
    assert run(dashboard,b)['status']=='DISPATCHING'
    assert len(transport['calls'])==1


def test_browser_manual_preview(dashboard,transport):
    from playwright.sync_api import sync_playwright,expect
    from tests.browser_navigation import navigate
    d=dashboard
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url)
            navigate(page,'integrations');page.on('dialog',lambda x:x.accept())
            page.fill('#n8nPassword',PASSWORD)
            page.locator('[data-action="n8n-report-preview"]').click()
            expect(page.locator('#n8nReportStatus')).to_contain_text('COMPLETED')
            expect(page.locator('#n8nReportResult')).to_contain_text('Journal entries')
            expect(page.locator('#n8nPassword')).to_have_value('')
            assert len(transport['calls'])==1
        finally:browser.close()


@pytest.mark.parametrize('phase',['INTENT','OUTCOME'])
def test_atomic_ledger_failure_after_write(dashboard,transport,monkeypatch,phase):
    original=report._ledger;b=body();d=dashboard
    def fail(store,con,row,current,*args):
        original(store,con,row,current,*args)
        if current==phase:raise RuntimeError('Simulated failure after ledger insert')
    monkeypatch.setattr(report,'_ledger',fail)
    with pytest.raises(RuntimeError):run(d,b)
    with d.store._connect() as con:
        phases=[json.loads(r[0])['phase'] for r in con.execute('SELECT record FROM decision_ledger WHERE correlation_id=?',(b['operation_id'],))]
        assert phases==([] if phase=='INTENT' else ['INTENT'])
        assert con.execute('SELECT count(*) FROM audit_log WHERE action=?',(report.ACTION,)).fetchone()[0]==len(phases)
    assert len(transport['calls'])==(0 if phase=='INTENT' else 1)
    if phase=='OUTCOME':assert run(d,b)['status']=='DISPATCHING' and len(transport['calls'])==1


def test_no_file_or_notification_delivery(dashboard,transport,monkeypatch):
    from notifications import report as legacy
    def forbidden(*a,**k):raise AssertionError('External or file delivery must not run')
    monkeypatch.setattr(legacy,'deliver_summaries',forbidden)
    with dashboard.store._connect() as con:
        before=[con.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('reports','notifications')]
    assert run(dashboard,body())['status']=='COMPLETED'
    with dashboard.store._connect() as con:
        assert before==[con.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('reports','notifications')]


@pytest.mark.parametrize('mode',['redirect','wrong-type','oversized','bad-json','wrong-key'])
def test_actual_http_boundaries(mode):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from threading import Thread
    from urllib.parse import urlsplit
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            data=b'x'*8193 if mode=='oversized' else b'not-json' if mode=='bad-json' else b'{}'
            status=302 if mode=='redirect' else 403 if mode=='wrong-key' else 200
            self.send_response(status)
            self.send_header('Content-Type','text/html' if mode=='wrong-type' else 'application/json')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with pytest.raises(ValueError):report._http(urlsplit(f'http://127.0.0.1:{server.server_port}'),report.PATH,key_name='X-Chief-Report-Key',key='synthetic',body={})
    finally:server.shutdown();server.server_close();thread.join()


def test_credentials_cannot_be_reused(dashboard,transport,monkeypatch):
    monkeypatch.setenv('CHIEF_N8N_REPORT_KEY','h'*40)
    with pytest.raises(ValueError):run(dashboard,body())
    assert transport['calls']==[]


def test_late_receipt_not_published(dashboard,transport,monkeypatch):
    original=report._http
    def late(*a,**k):
        result=original(*a,**k)
        if k.get('body') is not None:
            real=report.time.time();monkeypatch.setattr(report.time,'time',lambda:real+60)
        return result
    monkeypatch.setattr(report,'_http',late)
    assert run(dashboard,body())['status']=='UNKNOWN'


@pytest.mark.parametrize('kind,name',[('capability',report.CAPABILITY),('component',report.SERVICE)])
def test_report_controls_use_real_transition_adapter(dashboard,transport,kind,name):
    from application.control_services import compose_control_services
    from capabilities.contracts import Node,Mode
    d=dashboard;controls=compose_control_services(d.store).controls
    preview=controls.preview(Node(kind,name),Mode.DISABLED)
    controls.transition(preview,actor=d.credentials['principal'].id,reason='Disable reports',confirmed=True)
    with pytest.raises(PermissionError):run(d,body())
    assert transport['calls']==[]
    # A report-specific disable must not pause the existing domain worker.
    with d.store._connect() as con:
        row=con.execute("SELECT enabled,running FROM agent_controls WHERE domain='farming'").fetchone()
        assert tuple(row)==(1,1)
