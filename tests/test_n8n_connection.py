import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import pytest
from integrations import n8n
from test_chief_controls import request


@pytest.fixture
def remote(monkeypatch):
    observed=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            observed.append((self.path,self.headers.get('X-N8N-API-KEY')))
            body=json.dumps({'data':[{'id':'flow1','name':'Synthetic flow','active':False,'nodes':[{'secret':'MUST_NOT_LEAVE_CONNECTOR'}]}],'nextCursor':'next'}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('CHIEF_N8N_URL',f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setenv('CHIEF_N8N_API_KEY','synthetic-token')
    yield observed
    server.shutdown();server.server_close();thread.join()


def test_inventory_only_retains_metadata(remote):
    result=n8n.inventory()
    assert result['partial'] is True
    assert result['workflows']==[{'id':'flow1','name':'Synthetic flow','active':False}]
    assert 'MUST_NOT_LEAVE_CONNECTOR' not in json.dumps(result)
    assert remote==[('/api/v1/workflows?limit=100','synthetic-token')]


@pytest.mark.parametrize('url',['http://localhost:5678','https://example.com:443','http://127.0.0.1:5678/private','http://user:password@127.0.0.1:5678'])
def test_arbitrary_destinations_rejected(monkeypatch,url):
    monkeypatch.setenv('CHIEF_N8N_URL',url);monkeypatch.setenv('CHIEF_N8N_API_KEY','synthetic')
    with pytest.raises(ValueError):n8n.inventory()


def test_connection_control_and_sync(dashboard,remote):
    assert request(dashboard,'/api/integrations/n8n')[2]['enabled'] is False
    assert request(dashboard,'/api/integrations/n8n/check','POST',{})[0]==400
    code,_,data=request(dashboard,'/api/integrations/n8n','POST',{'enabled':True})
    assert code==200 and data['enabled'] is True
    assert request(dashboard,'/api/integrations/n8n/check','POST',{})[2]['status']=='CONNECTED'
    assert request(dashboard,'/api/integrations/n8n','POST',{'enabled':False})[2]['enabled'] is False
    assert len(remote)==2 # no implicit workflow writes or hidden requests


def test_disable_works_when_service_unavailable(dashboard,monkeypatch):
    monkeypatch.delenv('CHIEF_N8N_API_KEY',raising=False)
    assert request(dashboard,'/api/integrations/n8n','POST',{'enabled':False})[0]==200
    assert request(dashboard,'/api/integrations/n8n','POST',{'enabled':True})[0]==400


def test_redirect_never_followed(monkeypatch):
    class Response:
        status=302
    class Connection:
        def __init__(self,*a,**kw):pass
        def request(self,*a,**kw):pass
        def getresponse(self):return Response()
        def close(self):pass
    monkeypatch.setenv('CHIEF_N8N_URL','http://127.0.0.1:5678')
    monkeypatch.setenv('CHIEF_N8N_API_KEY','synthetic')
    monkeypatch.setattr(n8n.http.client,'HTTPConnection',Connection)
    with pytest.raises(ValueError):n8n.inventory()


def test_connection_api_requires_installation_permission():
    from application.auth_routes import requirement
    assert requirement('POST','/api/integrations/n8n',{'enabled':True},None)==('installation.manage',None,None,True)
    assert requirement('GET','/api/integrations/n8n',{},None)==('installation.manage',None,None,False)
