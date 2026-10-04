import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import pytest
from model_registry.setup import ModelSetup, NoRedirect
from private_secrets import imported
from private_secrets.service import SecretUnavailable
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_identity_http import request


def payload(**changes):
    body = dict(provider='groq', provider_model='synthetic-model', cost='UNKNOWN', api_key='')
    body.update(changes)
    return body


def test_native_encryption_private_metadata_and_backup_exclusion(tmp_path, capsys):
    from operations.backup import _asset_input, BackupError
    from operations.source_archive import check_archive_names
    setup = ModelSetup(tmp_path)
    value = 'synthetic-fixture-api-key-DO-NOT-EXPORT'
    record = setup.add(payload(api_key=value), actor='fixture-owner')
    path, private = setup._record(record['id'])
    assert imported.resolve(path, private['backend']) == value
    for file in path.iterdir():
        assert value.encode() not in file.read_bytes()
        with pytest.raises(BackupError):
            _asset_input(dict(path=str(file), logical_path='config/settings.json', kind='configuration'))
        with pytest.raises(ValueError):
            check_archive_names([file.relative_to(tmp_path).as_posix()])
    assert value not in json.dumps(setup.list())
    assert 'backend' not in record and 'api_key' not in record
    assert value not in ''.join(capsys.readouterr())
    key = path/('key.dpapi' if os.name=='nt' else 'key.cred')
    key.write_bytes(b'corrupt')
    with pytest.raises(SecretUnavailable):imported.resolve(path, private['backend'])


@pytest.mark.parametrize('name',['model-credentials/a/registration.json','x/key.dpapi','x/key.cred','MODEL-CREDENTIALS/a','x\\model-credentials\\a'])
def test_archive_guard_refuses_private_material(name):
    from operations.source_archive import check_archive_names
    with pytest.raises(ValueError):check_archive_names([name])
    check_archive_names(['private_secrets/imported.py','docs/Model-Setup.md'])


@pytest.mark.parametrize('changes',[
    {'provider':'https://attacker.invalid'}, {'provider_model':'<script>'},
    {'cost':'LEGACY_UNRESOLVED'}, {'state':'ENABLED'}, {'api_key':123},
    {'provider_model':'synthetic-key','api_key':'synthetic-key'}])
def test_invalid_input_never_publishes(tmp_path, changes):
    setup=ModelSetup(tmp_path)
    with pytest.raises(ValueError):setup.add(payload(**changes),actor='owner')
    assert setup.list()==[]


def test_failed_encryption_no_partial_or_plaintext_record(tmp_path, monkeypatch):
    def fail(*args):raise SecretUnavailable('Protected storage unavailable.')
    monkeypatch.setattr(imported,'provision',fail)
    setup=ModelSetup(tmp_path)
    with pytest.raises(SecretUnavailable):setup.add(payload(api_key='synthetic-only-key'),actor='owner')
    assert setup.list()==[] and list(setup.root.iterdir())==[]


def test_concurrent_registration_is_atomic_and_never_loses_records(tmp_path):
    setup=ModelSetup(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(lambda i:setup.add(payload(provider_model='fixture-'+str(i)),actor='owner'),range(8)))
    assert len(setup.list())==8 and len({r['id'] for r in rows})==8
    assert all(r['state']=='DISABLED' for r in rows)


def test_private_path_guard_and_source_exclusion(tmp_path):
    from operations.backup import BackupError
    with pytest.raises(ValueError):ModelSetup(Path(__file__).resolve().parents[1])
    if os.name!='nt':
        root=tmp_path/'linked';root.symlink_to(tmp_path,target_is_directory=True)
        with pytest.raises(BackupError):ModelSetup(root)
        setup=ModelSetup(tmp_path);setup.root.mkdir(mode=0o755);setup.root.chmod(0o755)
        with pytest.raises(PermissionError):setup.add(payload(),actor='owner')


@pytest.mark.parametrize('role,scope,expected',[
    ('Owner',('*',),201),('Administrator',('*',),201),
    ('Administrator',('jobs',),403),('Manager',('jobs',),403),('Worker',('jobs',),403)])
def test_setup_server_permission_matrix(dashboard,role,scope,expected):
    d=dashboard;s=IdentityService(d.store)
    s.create_user(d.credentials['principal'],'setup-operator',PASSWORD,role,scope)
    raw,_=s.login('setup-operator',PASSWORD)
    assert request(d,'/api/model-setup','POST',payload(),raw)[0]==expected


def test_http_auth_csrf_reauth_and_routing_unchanged(dashboard):
    d=dashboard;raw=d.credentials['raw']
    before=request(d,'/api/ui/models',raw=raw)[2]
    assert request(d,'/api/model-setup','POST',payload())[0]==401
    for change in ({'csrf':False},{'origin':False},{'extra':{'Host':'attacker.invalid'}}):
        assert request(d,'/api/model-setup','POST',payload(),raw,**change)[0]==403
    with d.store._connect() as con:
        con.execute('UPDATE human_sessions SET reauth_at=? WHERE id=?',(utc_text(utc_now()-timedelta(minutes=6)),d.credentials['principal'].session_id))
    assert request(d,'/api/model-setup','POST',payload(),raw)[0]==428
    status,headers,_=request(d,'/api/auth/reauthenticate','POST',{'password':PASSWORD},raw)
    assert status==200
    raw=headers['Set-Cookie'].split(';')[0].split('=',1)[1]
    status,_,record=request(d,'/api/model-setup','POST',payload(cost='PAID'),raw)
    assert status==201 and record['state']=='DISABLED'
    after=request(d,'/api/ui/models',raw=raw)[2]
    assert all(after[k]==before[k] for k in ('models','policy','job_assignment','eligible_route_models'))
    assert after['registrations']==[record]
    with d.store._connect() as con:
        assert con.execute('SELECT COUNT(*) FROM model_assignments').fetchone()[0]==0
        event=con.execute("SELECT human_id,resource FROM human_security_events WHERE operation='MODEL_REGISTERED'").fetchone()
        assert tuple(event)==(d.credentials['principal'].id,record['id'])


def test_connection_check_is_explicit_fixed_and_redacted(tmp_path,monkeypatch):
    setup=ModelSetup(tmp_path)
    monkeypatch.setattr(imported,'provision',lambda *a:'synthetic-backend')
    monkeypatch.setattr(imported,'resolve',lambda *a:'synthetic-sensitive-value')
    row=setup.add(payload(api_key='synthetic-sensitive-value'),actor='owner')
    calls=[]
    class Reply:
        status=200
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,limit):return json.dumps({'data':[{'id':'synthetic-model'}]}).encode()
    def open_request(req,timeout):
        calls.append(req.full_url)
        assert req.get_header('Authorization')=='Bearer synthetic-sensitive-value' and timeout==10
        return Reply()
    import urllib.request
    monkeypatch.setattr(urllib.request,'build_opener',lambda *handlers:SimpleNamespace(open=open_request))
    assert calls==[]
    result=setup.check_connection(row['id'])
    assert result['status']=='CONNECTED' and result['model_listed']
    assert calls==['https://api.groq.com/openai/v1/models']
    assert setup.list()[0]['state']=='DISABLED'
    def failure(*a,**kw):raise ValueError('synthetic-sensitive-value')
    monkeypatch.setattr(urllib.request,'build_opener',failure)
    result=setup.check_connection(row['id'])
    assert result['status']=='CHECK_FAILED' and 'synthetic-sensitive-value' not in json.dumps(result)
    assert NoRedirect().redirect_request(None,None,302,'',{},'https://attacker.invalid') is None


def test_check_requires_confirmation_and_preview_refuses(dashboard,monkeypatch):
    d=dashboard;raw=d.credentials['raw']
    record=request(d,'/api/model-setup','POST',payload(),raw)[2]
    path='/api/model-setup/'+record['id']+'/check'
    assert request(d,path,'POST',{},raw)[0]==400
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','preview')
    assert request(d,path,'POST',{'confirmed':True},raw)[0]==403


def test_insecure_nonlocal_setup_refused_before_storage(dashboard,monkeypatch):
    d=dashboard;original=d.app.Handler.setup
    def remote(handler):
        original(handler);handler.client_address=('192.168.12.5',1234)
    monkeypatch.setattr(d.app.Handler,'setup',remote)
    code,_,result=request(d,'/api/model-setup','POST',payload(api_key='synthetic-key'),d.credentials['raw'])
    assert code==403 and 'synthetic-key' not in json.dumps(result)
    assert ModelSetup(Path(os.environ['CHIEF_STATE_ROOT'])).list()==[]


def test_browser_failed_save_and_navigation_clear_keys(dashboard,monkeypatch):
    from playwright.sync_api import sync_playwright,expect
    def unavailable(*a):raise SecretUnavailable('Protected key storage is unavailable.')
    monkeypatch.setattr(imported,'provision',unavailable)
    d=dashboard
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page();page.goto(d.url)
        expect(page.locator('#navLinks')).to_be_visible();page.locator('#navLinks button[data-target="models"]').click()
        page.locator('#setupProvider').fill('groq');page.locator('#setupModel').fill('synthetic-model')
        page.locator('#setupKey').fill('synthetic-sensitive-value');page.locator('#setupPassword').fill(PASSWORD)
        page.get_by_role('button',name='Save inactive model',exact=True).click()
        expect(page.locator('#modelSetupResult')).to_contain_text('unavailable')
        expect(page.locator('#setupKey')).to_have_value('');expect(page.locator('#setupPassword')).to_have_value('')
        assert ModelSetup(Path(os.environ['CHIEF_STATE_ROOT'])).list()==[]
        page.locator('#setupKey').fill('synthetic-sensitive-value');page.locator('#setupPassword').fill(PASSWORD)
        page.locator('#navLinks button[data-target="overview"]').click()
        expect(page.locator('#setupKey')).to_have_value('');expect(page.locator('#setupPassword')).to_have_value('')
        browser.close()


def test_browser_save_clears_secret_fields_and_preserves_routing(dashboard,monkeypatch):
    from playwright.sync_api import sync_playwright,expect
    monkeypatch.setattr(imported,'provision',lambda *a:'synthetic-backend')
    d=dashboard
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page();page.goto(d.url)
        expect(page.locator('#navLinks')).to_be_visible()
        page.locator('#navLinks button[data-target="models"]').click()
        expect(page.locator('#setupKey')).to_be_visible()
        page.locator('#setupProvider').fill('groq');page.locator('#setupModel').fill('synthetic-model')
        page.locator('#setupKey').fill('synthetic-sensitive-value');page.locator('#setupPassword').fill(PASSWORD)
        page.get_by_role('button',name='Save inactive model',exact=True).click()
        expect(page.locator('#modelSetupResult')).to_contain_text('Saved inactive')
        expect(page.locator('#setupKey')).to_have_value('');expect(page.locator('#setupPassword')).to_have_value('')
        assert page.evaluate('localStorage.length')==0
        assert 'synthetic-sensitive-value' not in page.content()
        with d.store._connect() as con:
            assert 'synthetic-sensitive-value' not in json.dumps([dict(r) for r in con.execute('SELECT * FROM human_security_events')])
        browser.close()
