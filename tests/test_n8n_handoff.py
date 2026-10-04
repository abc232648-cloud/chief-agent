import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread, Event
from concurrent.futures import ThreadPoolExecutor
import pytest
from integrations import n8n, n8n_handoff as handoff
from application.composition import default_registry
from test_chief_controls import request

TOKEN='synthetic-handshake-credential-32-characters'

@pytest.fixture
def receiver(dashboard,monkeypatch):
    state={'calls':[], 'mode':'ok'}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['calls'].append({'path':self.path,'body':body,'key':self.headers.get('X-Chief-Handshake-Key'),'inventory_key':self.headers.get('X-N8N-API-KEY')})
            mode=state['mode']
            if mode=='disconnect':self.close_connection=True;return
            status=401 if mode=='denied' else 302 if mode=='redirect' else 200
            result={'contract':handoff.CONTRACT,'operation_id':body['operation_id'],'domain':body['domain'],'status':'COMPLETED','result':'synthetic-handshake-only'}
            if mode=='wrong-id':result['operation_id']='0'*32
            if mode=='wrong-contract':result['contract']='unexpected'
            if mode=='accepted':result={'message':'Workflow got started'}
            raw=json.dumps(result).encode() if mode!='oversized' else b'x'*4097
            self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('CHIEF_N8N_URL',f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setenv('CHIEF_N8N_API_KEY','inventory-only-synthetic')
    monkeypatch.setenv('CHIEF_N8N_HANDSHAKE_KEY',TOKEN)
    with dashboard.store._connect() as con:con.execute('INSERT INTO control_state VALUES(?,?)',(n8n.KEY,'true'))
    yield state
    server.shutdown();server.server_close();thread.join()


def body():return {'operation_id':uuid.uuid4().hex,'domain':'farming','confirmed':True}


def run(d,b):return handoff.run(d.store,default_registry(),d.credentials['principal'],b)


def test_http_success_duplicate_and_minimal_payload(dashboard,receiver):
    b=body();result=request(dashboard,'/api/integrations/n8n/handshake','POST',b)
    assert result[0]==200 and result[2]['status']=='COMPLETED'
    assert request(dashboard,'/api/integrations/n8n/handshake','POST',b)[2]['status']=='COMPLETED'
    assert len(receiver['calls'])==1
    sent=receiver['calls'][0]
    assert sent['path']==handoff.PATH and sent['key']==TOKEN and sent['inventory_key'] is None
    assert set(sent['body'])=={'contract','operation_id','domain','deadline','purpose'}
    assert request(dashboard,'/api/integrations/n8n/handshakes')[2]['items'][0]['operation_id']==b['operation_id']
    with dashboard.store._connect() as con:
        assert TOKEN not in str([tuple(r) for r in con.execute('SELECT * FROM audit_log')])
        assert TOKEN not in str([tuple(r) for r in con.execute('SELECT * FROM control_state')])


@pytest.mark.parametrize('mode',['disconnect','denied','redirect','wrong-id','wrong-contract','accepted','oversized'])
def test_uncertain_outcome_never_retries(dashboard,receiver,mode):
    receiver['mode']=mode;b=body()
    assert run(dashboard,b)['status']=='UNKNOWN'
    assert run(dashboard,b)['status']=='UNKNOWN'
    assert len(receiver['calls'])==1


def test_concurrent_duplicate_claim(dashboard,receiver,monkeypatch):
    entered=Event();release=Event();original=handoff._exchange
    def blocked(*args):entered.set();assert release.wait(10);return original(*args)
    monkeypatch.setattr(handoff,'_exchange',blocked);b=body()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(run,dashboard,b)
        assert entered.wait(5)
        second=pool.submit(run,dashboard,b).result(timeout=5)
        assert second['status']=='DISPATCHING'
        release.set();assert first.result(timeout=10)['status']=='COMPLETED'
    assert len(receiver['calls'])==1


def test_crash_after_intent_survives_restart_without_send(dashboard,receiver,monkeypatch):
    def crash(*args):raise SystemExit('synthetic process interruption')
    monkeypatch.setattr(handoff,'_exchange',crash);b=body()
    with pytest.raises(SystemExit):run(dashboard,b)
    with dashboard.store._connect() as con:
        row=json.loads(con.execute('SELECT value FROM control_state WHERE key=?',(handoff.PREFIX+b['operation_id'],)).fetchone()[0]);row['deadline']=time.time()-1
        con.execute('UPDATE control_state SET value=? WHERE key=?',(json.dumps(row),handoff.PREFIX+b['operation_id']))
    assert run(dashboard,b)['status']=='UNKNOWN'
    assert receiver['calls']==[]


@pytest.mark.parametrize('gate',['disabled','domain-paused','creator-disabled','credential-reused'])
def test_gates_before_dispatch(dashboard,receiver,monkeypatch,gate):
    with dashboard.store._connect() as con:
        if gate=='disabled':con.execute('UPDATE control_state SET value=? WHERE key=?',('false',n8n.KEY))
        if gate=='domain-paused':con.execute("UPDATE agent_controls SET running=0 WHERE domain='farming'")
        if gate=='creator-disabled':con.execute('UPDATE human_identities SET enabled=0 WHERE id=?',(dashboard.credentials['principal'].id,))
    if gate=='credential-reused':monkeypatch.setenv('CHIEF_N8N_API_KEY',TOKEN)
    with pytest.raises((ValueError,PermissionError)):run(dashboard,body())
    assert receiver['calls']==[]


def test_changed_domain_cannot_reuse_operation(dashboard,receiver):
    b=body();run(dashboard,b);b['domain']='jobs'
    with pytest.raises(ValueError):run(dashboard,b)
    assert len(receiver['calls'])==1


def test_route_csrf_and_fresh_auth(dashboard,receiver):
    from test_identity_http import request as raw_request
    from operations.time_integrity import utc_now,utc_text
    from datetime import timedelta
    raw=dashboard.credentials['raw']
    assert raw_request(dashboard,'/api/integrations/n8n/handshake','POST',body(),raw,csrf=False)[0]==403
    with dashboard.store._connect() as con:con.execute('UPDATE human_sessions SET reauth_at=?',(utc_text(utc_now()-timedelta(minutes=6)),))
    assert raw_request(dashboard,'/api/integrations/n8n/handshake','POST',body(),raw)[0]==428
    assert receiver['calls']==[]


def test_payload_rejects_arbitrary_workflow(dashboard,receiver):
    b=body();b['url']='https://example.invalid/workflow'
    assert request(dashboard,'/api/integrations/n8n/handshake','POST',b)[0]==400
    assert receiver['calls']==[]


def test_response_timeout_is_unknown(dashboard,receiver,monkeypatch):
    class TimeoutConnection:
        def __init__(self,*args,**kwargs):pass
        def request(self,*args,**kwargs):raise TimeoutError('synthetic socket timeout')
        def close(self):pass
    monkeypatch.setattr(handoff.http.client,'HTTPConnection',TimeoutConnection)
    b=body();assert run(dashboard,b)['status']=='UNKNOWN'
    assert run(dashboard,b)['status']=='UNKNOWN'


def test_fixture_workflow_validates_without_business_nodes(tmp_path):
    import subprocess,shutil
    from pathlib import Path
    template=json.loads(Path('integrations/workflows/chief-handshake-v1.json').read_text())
    assert template['active'] is False
    assert [n['type'] for n in template['nodes']]==['n8n-nodes-base.webhook','n8n-nodes-base.code','n8n-nodes-base.respondToWebhook']
    assert template['nodes'][0]['parameters']['authentication']=='headerAuth'
    node=shutil.which('node');assert node,'Node required to validate exported workflow code'
    code=template['nodes'][1]['parameters']['jsCode']
    script=tmp_path/'workflow-check.js'
    script.write_text('const fn = new Function("$json",'+json.dumps(code)+');\n'+'''
const assert=require('node:assert/strict');
const good={contract:'chief.handshake.v1',purpose:'synthetic-handshake-only',operation_id:'a'.repeat(32),domain:'farming',deadline:Date.now()/1000+15};
assert.equal(fn({body:good})[0].json.status,'COMPLETED');
for(const bad of [{...good,deadline:0},{...good,contract:'other'},{...good,command:'send-money'},{...good,operation_id:'invalid'}]) assert.throws(()=>fn({body:bad}));
''')
    subprocess.run([node,str(script)],check=True,capture_output=True,text=True)


@pytest.mark.parametrize('width',[390,1280])
def test_handshake_browser_and_history(dashboard,receiver,width):
    from playwright.sync_api import sync_playwright,expect
    from browser_navigation import navigate
    from tests.checkpoint_f_fixture import PASSWORD
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':width,'height':850});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('dialog',lambda d:d.accept())
        page.goto(dashboard.url)
        expect(page.locator('nav button[data-target="integrations"]')).to_have_count(1)
        navigate(page,'integrations')
        page.locator('#n8nPassword').fill(PASSWORD)
        page.locator('#n8nTestDomain').select_option('farming')
        page.locator('[data-action="n8n-handshake"]').click()
        expect(page.locator('#n8nHandshakeStatus')).to_contain_text('COMPLETED')
        expect(page.locator('#n8nPassword')).to_have_value('')
        page.locator('[data-action="n8n-history"]').click()
        expect(page.locator('#n8nHistory')).to_contain_text('COMPLETED')
        assert len(receiver['calls'])==1
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert not errors
        browser.close()


@pytest.mark.parametrize('stage',['intent','outcome'])
def test_audit_write_failure_never_causes_automatic_resend(dashboard,receiver,stage):
    import sqlite3
    action='Dispatch synthetic n8n handshake' if stage=='intent' else 'Synthetic n8n handshake outcome'
    with dashboard.store._connect() as con:
        con.execute("CREATE TRIGGER fail_handoff_audit BEFORE INSERT ON audit_log WHEN NEW.action='"+action+"' BEGIN SELECT RAISE(ABORT,'synthetic storage failure'); END")
    b=body()
    with pytest.raises(sqlite3.IntegrityError):run(dashboard,b)
    if stage=='intent':
        assert receiver['calls']==[]
        assert handoff.history(dashboard.store)==[]
    else:
        assert len(receiver['calls'])==1
        assert run(dashboard,b)['status']=='DISPATCHING'
        assert len(receiver['calls'])==1


def test_non_admin_cannot_trigger_or_read_history(dashboard,receiver):
    from test_identity_http import request as raw_request
    from identity.service import IdentityService
    from tests.checkpoint_f_fixture import PASSWORD
    service=IdentityService(dashboard.store)
    service.create_user(dashboard.credentials['principal'],'handoff-worker',PASSWORD,'Worker',('farming',))
    raw,_=service.login('handoff-worker',PASSWORD)
    assert raw_request(dashboard,'/api/integrations/n8n/handshake','POST',body(),raw)[0]==403
    assert raw_request(dashboard,'/api/integrations/n8n/handshakes',raw=raw)[0]==403
    assert receiver['calls']==[]
