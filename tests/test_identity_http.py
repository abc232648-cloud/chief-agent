"""Raw clients deliberately bypass the authenticated legacy-test client helper."""
import http.client,json
from datetime import timedelta
from urllib.parse import urlparse
import pytest
from identity.service import IdentityService
from operations.time_integrity import utc_now,utc_text
from tests.checkpoint_f_fixture import PASSWORD
from application.auth_routes import private_bind


class FixtureConnection(http.client.HTTPConnection):
    """Send each finite synthetic request together, never retry a mutation.

    Header-only denials can close before http.client's separate body send on
    Windows. Coalescing keeps these assertions about the actual HTTP response,
    rather than the timing of a second send after the server has rejected it.
    """
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.parts=[]

    def send(self,data):
        assert isinstance(data,(bytes,bytearray))
        self.parts.append(data)

    def flush_request(self):
        super().send(b''.join(self.parts))
        self.parts.clear()

def request(d,path,method='GET',body=None,raw=None,csrf=True,origin=True,extra=None):
    url=urlparse(d.url);headers={'Content-Type':'application/json'}
    if raw:
        headers['Cookie']='chief_session='+raw
        if csrf:headers['X-Chief-CSRF']=IdentityService.csrf(raw)
    if origin:headers['Origin']=d.url
    headers.update(extra or {})
    client=FixtureConnection(url.hostname,url.port,timeout=10)
    try:
        client.request(method,path,body=None if body is None else json.dumps(body),headers=headers)
        client.flush_request()
        result=client.getresponse();data=result.read()
        return result.status,dict(result.getheaders()),json.loads(data) if data and 'application/json' in result.getheader('Content-Type','') else data
    finally:client.close()

@pytest.mark.parametrize('path',['/api/state','/api/actions','/api/facts','/api/model-controls','/api/domains/farming','/api/auth/session'])
def test_unauthenticated_api_rejected(dashboard,path):
    assert request(dashboard,path)[0]==401
    assert request(dashboard,path,raw='invalid-session-token-that-is-long-enough')[0]==401

def test_login_cookie_logout_and_missing_schema(dashboard,tmp_path):
    d=dashboard
    assert request(d,'/')[0]==303
    assert request(d,'/login')[0]==200
    body={'username':'fixture-owner','password':PASSWORD}
    assert request(d,'/api/auth/login','POST',body,origin=False)[0]==403
    status,headers,payload=request(d,'/api/auth/login','POST',body)
    assert status==200 and 'HttpOnly' in headers['Set-Cookie'] and 'SameSite=Strict' in headers['Set-Cookie']
    raw=headers['Set-Cookie'].split(';')[0].split('=',1)[1]
    assert payload['csrf']==IdentityService.csrf(raw)
    assert request(d,'/api/auth/session',raw=raw)[0]==200
    assert request(d,'/api/auth/logout','POST',{},raw)[0]==200
    assert request(d,'/api/auth/session',raw=raw)[0]==401
    from database.store import Store
    d.app.STORE=Store(tmp_path/'unmigrated.db')
    assert request(d,'/api/state',raw=raw)[0]==503

@pytest.mark.parametrize('role,global_scope,expected',[
    ('Owner',True,200),('Administrator',True,200),('Manager',False,403),('Worker',False,403),('Administrator',False,403)])
def test_privileged_model_setting_matrix(dashboard,role,global_scope,expected):
    d=dashboard;s=IdentityService(d.store)
    s.create_user(d.credentials['principal'],'operator',PASSWORD,role,('*',) if global_scope else ('jobs',))
    raw,_=s.login('operator',PASSWORD)
    assert request(d,'/api/model-controls','POST',{'model':'jobs.qwen','state':'DISABLED'},raw)[0]==expected
    if expected==403:
        from application.auth_routes import model_registry
        assert model_registry(d.store).state('jobs.qwen').value!='DISABLED'

def test_domain_scope_aggregate_denial_and_audit_identity(dashboard):
    d=dashboard;s=IdentityService(d.store)
    identity=s.create_user(d.credentials['principal'],'manager',PASSWORD,'Manager',('jobs',));raw,_=s.login('manager',PASSWORD)
    assert request(d,'/api/jobs',raw=raw)[0]==200
    assert request(d,'/api/domains/farming',raw=raw)[0]==403
    assert request(d,'/api/state',raw=raw)[0]==403
    assert request(d,'/api/auth/users','POST',{'username':'other','password':PASSWORD,'role':'Owner','domains':['*']},raw)[0]==403
    fact=d.store.add_candidate_fact({'text':'Synthetic proposed fact'})
    assert request(d,'/api/facts/'+str(fact),'POST',{'status':'USER_CONFIRMED'},raw)[0]==403
    with d.store._connect() as con:
        assert con.execute("SELECT human_id FROM human_security_events WHERE operation='work.read' AND outcome='AUTHORIZED' ORDER BY id DESC LIMIT 1").fetchone()[0]==identity

@pytest.mark.parametrize('change',[{'csrf':False},{'origin':False},{'extra':{'Origin':'https://attacker.invalid'}},{'extra':{'Host':'attacker.invalid'}},{'extra':{'Sec-Fetch-Site':'cross-site'}}])
def test_csrf_origin_host_still_enforced(dashboard,change):
    before=request(dashboard,'/api/model-controls',raw=dashboard.credentials['raw'])[2]
    assert request(dashboard,'/api/model-controls','POST',{'model':'jobs.qwen','state':'DISABLED'},dashboard.credentials['raw'],**change)[0]==403
    assert request(dashboard,'/api/model-controls',raw=dashboard.credentials['raw'])[2]==before

def test_reauthentication_approval_identity_and_emergency_pause(dashboard):
    d=dashboard;s=IdentityService(d.store);raw=d.credentials['raw'];principal=d.credentials['principal']
    with d.store._connect() as con:con.execute('UPDATE human_sessions SET reauth_at=? WHERE id=?',(utc_text(utc_now()-timedelta(minutes=6)),principal.session_id))
    assert request(d,'/api/model-controls','POST',{'model':'jobs.qwen','state':'DISABLED'},raw)[0]==428
    assert request(d,'/api/auth/reauthenticate','POST',{'password':'wrong-password'},raw)[0]==401
    status,headers,result=request(d,'/api/auth/reauthenticate','POST',{'password':PASSWORD},raw)
    assert status==200
    old_raw=raw;raw=headers['Set-Cookie'].split(';')[0].split('=',1)[1]
    assert raw!=old_raw and result['csrf']==s.csrf(raw)
    assert request(d,'/api/auth/session',raw=old_raw)[0]==401
    action=d.store.add_action('Synthetic approval','pay_money')
    assert request(d,'/api/actions/'+str(action),'POST',{'status':'APPROVED'},raw)[0]==200
    with d.store._connect() as con:assert con.execute('SELECT human_id FROM human_action_approvals WHERE action_id=?',(action,)).fetchone()[0]==principal.id
    worker=s.create_user(principal,'worker',PASSWORD,'Worker',('jobs',));worker_raw,_=s.login('worker',PASSWORD)
    assert request(d,'/api/agent-controls/jobs','POST',{'running':False},worker_raw)[0]==403
    s.grant(principal,worker,'jobs',emergency=True)
    assert request(d,'/api/agent-controls/jobs','POST',{'running':False},worker_raw)[0]==200
    assert request(d,'/api/agent-controls/jobs','POST',{'running':False,'enabled':False},worker_raw)[0]==403
    assert request(d,'/api/agent-controls/jobs','POST',{'running':True},worker_raw)[0]==403

@pytest.mark.parametrize('host',['0.0.0.0','::','8.8.8.8','224.0.0.1'])
def test_no_public_or_wildcard_bind(host):
    with pytest.raises(ValueError):private_bind(host)

@pytest.mark.parametrize('host',['127.0.0.1','::1','localhost','192.168.1.8'])
def test_explicit_private_bind(host):assert private_bind(host)==host
