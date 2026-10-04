from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path
from types import SimpleNamespace
import urllib.error
import urllib.request
import pytest

def request(dashboard,path,method='GET',body=None):
    data=None if body is None else json.dumps(body).encode()
    req=urllib.request.Request(dashboard.url+path,data=data,method=method,headers={'Content-Type':'application/json'})
    try: response=urllib.request.urlopen(req,timeout=5)
    except urllib.error.HTTPError as exc: response=exc
    with response:
        raw=response.read()
        return response.status,dict(response.headers),json.loads(raw) if raw and response.headers.get_content_type()=='application/json' else raw

@pytest.mark.parametrize('path,mime',[('/','text/html'),('/static/dashboard.js','application/javascript'),('/static/dashboard.css','text/css')])
def test_http_assets_are_exact_bytes_and_head_matches(dashboard,path,mime):
    code,headers,body=request(dashboard,path)
    file=dashboard.root/('dashboard.html' if path=='/' else path.lstrip('/'))
    assert code==200 and headers['Content-Type'].startswith(mime)
    assert body==file.read_bytes() and headers['Cache-Control']=='no-store'
    head_code,head_headers,head_body=request(dashboard,path,'HEAD')
    assert head_code==200 and head_body==b''
    assert head_headers['Content-Type']==headers['Content-Type']
    assert int(head_headers['Content-Length'])==len(body)
    if path=='/': assert "script-src 'self'; style-src 'self'" in headers['Content-Security-Policy']

@pytest.mark.parametrize('path',['/static/../.env','/static/%2e%2e/.env','/static/.env','/static/missing.js','/api/missing'])
def test_missing_and_traversal_routes(dashboard,path):
    assert request(dashboard,path)[0]==404

@pytest.mark.parametrize('path',['state','actions','jobs','applications','sources','reports','notifications','cvs','profiles','facts','audit','scheduler','settings/email'])
def test_dashboard_get_routes(dashboard,path):
    code,headers,data=request(dashboard,'/api/'+path)
    assert code==200 and headers['Content-Type']=='application/json'
    assert isinstance(data,(dict,list))

def test_email_route_does_not_expose_password(dashboard,monkeypatch):
    monkeypatch.setenv('SMTP_PASSWORD','must-not-leak')
    data=request(dashboard,'/api/settings/email')[2]
    assert data['password_configured'] is True
    assert 'must-not-leak' not in json.dumps(data)

def test_html_unique_ids_and_navigation_targets():
    class Parser(HTMLParser):
        def __init__(self): super().__init__(); self.ids=[];self.sections=[];self.targets=[]
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if 'id' in attrs: self.ids.append(attrs['id'])
            if tag=='section': self.sections.append(attrs['id'])
            if attrs.get('data-action')=='show': self.targets.append(attrs['data-target'])
    parser=Parser();parser.feed((Path(__file__).resolve().parents[1]/'dashboard.html').read_text())
    assert not [key for key,count in Counter(parser.ids).items() if count>1]
    assert set(parser.targets)<=set(parser.sections)
    from application.composition import default_registry
    registered={page for domain in default_registry().describe() for page,label in domain['pages']}
    assert registered<=set(parser.sections)

def test_bad_requests_return_json_instead_of_dropping_connection(dashboard):
    for path,body in [('/api/command',[]),('/api/command',{}),('/api/facts/not-a-number',{}),('/api/profiles',{'check_interval_days':'bad'})]:
        code,headers,data=request(dashboard,path,'POST',body)
        assert code==400 and data['reason']
    req=urllib.request.Request(dashboard.url+'/api/command',data=b'{broken',method='POST')
    with pytest.raises(urllib.error.HTTPError) as error: urllib.request.urlopen(req)
    assert error.value.code==400 and json.load(error.value)['status']=='REJECTED'

def test_server_fault_is_json_and_does_not_leak_exception(dashboard,monkeypatch):
    def broken(**kwargs): raise RuntimeError('secret-in-server-error')
    monkeypatch.setattr(dashboard.store,'jobs',broken)
    code,headers,data=request(dashboard,'/api/jobs')
    assert code==500 and 'secret-in-server-error' not in json.dumps(data)

def test_profile_remove_wiring(dashboard):
    code,_,data=request(dashboard,'/api/profiles','POST',{'label':'Test','url':'https://example.test/profile','check_interval_days':7})
    assert code==200
    pid=data['id']
    request(dashboard,f'/api/profiles/{pid}','POST',{'enabled':False})
    assert dashboard.store.profile_links()[0]['enabled']==0
    assert request(dashboard,f'/api/profiles/{pid}','DELETE')[0]==200
    assert dashboard.store.profile_links()==[]

def test_restart_source_seed_preserves_user_verification(dashboard):
    dashboard.app.ROOT=dashboard.root
    for source in dashboard.store.sources():
        source['verification_status']='APPROVED';source['notes']='User reviewed'
        dashboard.store.add_source(source)
    dashboard.app.seed_sources()
    assert all(s['verification_status']=='APPROVED' and s['notes']=='User reviewed' for s in dashboard.store.sources())

def test_dashboard_approval_worker_claim_and_gate(dashboard):
    from worker.command_processor import CommandProcessor
    from worker.runner import run_approved_once
    calls=[]
    class Executor:
        def prepare(self,*args,**kwargs): calls.append((args,kwargs));return {'status':'FILLED'}
    processor=CommandProcessor(dashboard.store,None,application_executor=Executor())
    aid=dashboard.store.add_action('Fill test form','fill_application_form',payload={'url':'https://example.test','fields':[]})
    assert processor.process_approved_action(aid,claimed=True)['status']=='FAILED' and calls==[]
    code,_,data=request(dashboard,f'/api/actions/{aid}','POST',{'status':'APPROVED'})
    assert code==200 and data['status']=='APPROVED' and calls==[]
    assert request(dashboard,f'/api/actions/{aid}','POST',{'status':'APPROVED'})[0]==409
    assert run_approved_once(processor) is True
    assert len(calls)==1 and calls[0][1]['approved'] is True
    assert dashboard.store.get_action(aid)['status']=='DONE'
    assert run_approved_once(processor) is False
    forbidden=dashboard.store.add_action('Forbidden','pay_money')
    request(dashboard,f'/api/actions/{forbidden}','POST',{'status':'APPROVED'})
    assert run_approved_once(processor) is True and len(calls)==1
    assert dashboard.store.audit()[0]['status']=='BLOCKED'

def test_dashboard_command_reaches_actual_worker_processor(dashboard):
    from worker.command_processor import CommandProcessor
    from worker.runner import run_once
    class Gateway:
        def generate(self,req):
            return SimpleNamespace(text=json.dumps({'summary':'Controlled test','actions':[{'action':'pay_money','payload':{}},{'action':'fill_application_form','payload':{'url':'https://example.test','fields':[]}}]}))
    request(dashboard,'/api/command','POST',{'instruction':'Controlled integration test'})
    assert run_once(CommandProcessor(dashboard.store,Gateway())) is True
    command=dashboard.store.commands()[0]
    assert command['status']=='FAILED'  # The forbidden step must not be reported as completed.
    results=json.loads(command['result'])['results']
    assert results[0]['status']=='BLOCKED' and results[1]['status']=='AWAITING_APPROVAL'
    assert request(dashboard,'/api/actions')[2][0]['status']=='PENDING'

def test_container_packages_separated_assets():
    text=(Path(__file__).resolve().parents[1]/'gateway/Dockerfile').read_text()
    assert 'COPY dashboard.html ./dashboard.html' in text and 'COPY static ./static' in text
