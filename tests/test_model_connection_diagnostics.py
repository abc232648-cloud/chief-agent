"""Safe actionable classifications: never expose provider bodies or key errors."""
import json
import ssl
import urllib.error
from types import SimpleNamespace
import pytest
from model_registry.setup import ModelSetup
from private_secrets import imported


@pytest.mark.parametrize('error,code',[
    (urllib.error.HTTPError('https://synthetic.invalid',401,'SECRET',{},None),'AUTH_REJECTED'),
    (urllib.error.HTTPError('https://synthetic.invalid',403,'SECRET',{},None),'ACCESS_DENIED'),
    (urllib.error.HTTPError('https://synthetic.invalid',429,'SECRET',{},None),'RATE_LIMITED'),
    (urllib.error.HTTPError('https://synthetic.invalid',500,'SECRET',{},None),'PROVIDER_HTTP_ERROR'),
    (urllib.error.URLError('SECRET'),'NETWORK_FAILED'),
    (urllib.error.URLError(ssl.SSLError('SECRET')),'TLS_FAILED'),
    (ValueError('SECRET'),'INVALID_RESPONSE'),
])
def test_provider_failure_classification_and_redaction(tmp_path,monkeypatch,error,code):
    setup=ModelSetup(tmp_path)
    monkeypatch.setattr(setup,'_record',lambda identity:(tmp_path,{'provider':'groq','key_configured':True,'backend':'synthetic','provider_model':'qwen/synthetic'}))
    monkeypatch.setattr(imported,'resolve',lambda *args:'SECRET')
    def fail(*args,**kwargs):raise error
    monkeypatch.setattr('urllib.request.build_opener',lambda *args:SimpleNamespace(open=fail))
    result=setup.check_connection('synthetic')
    assert result['code']==code and result['status']=='CHECK_FAILED'
    assert 'SECRET' not in json.dumps(result) and 'synthetic.invalid' not in json.dumps(result)


def test_key_unlock_failure_never_contacts_provider(tmp_path,monkeypatch):
    setup=ModelSetup(tmp_path)
    monkeypatch.setattr(setup,'_record',lambda identity:(tmp_path,{'provider':'groq','key_configured':True,'backend':'synthetic'}))
    def fail(*args):raise ValueError('SECRET')
    monkeypatch.setattr(imported,'resolve',fail)
    monkeypatch.setattr('urllib.request.build_opener',lambda *args:pytest.fail('Must not contact provider'))
    result=setup.check_connection('synthetic')
    assert result['code']=='KEY_UNAVAILABLE' and 'SECRET' not in json.dumps(result)


@pytest.mark.usefixtures('configured_both_workspace')
def test_job_home_has_workspace_links_without_reopening_itself(dashboard):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url)
            page.locator('#chiefAgents button[data-id="jobs"]').click()
            expect(page.locator('#jobOverview h1')).to_have_text('Job Agent')
            assert page.locator('#jobOverview button[data-action="open-agent"]').count()==0
            expect(page.locator('#jobWorkspaceButtons').get_by_role('button',name='Opportunities',exact=True)).to_be_visible()
        finally:browser.close()


@pytest.mark.usefixtures('configured_both_workspace')
@pytest.mark.parametrize('agent,page_id,title',[('jobs','sources','Job Agent'),('farming','farmRecords','Farm Agent')])
@pytest.mark.parametrize('home_selector',['#workspaceName','button.brand'])
def test_agent_header_and_brand_return_to_agent_home(dashboard,agent,page_id,title,home_selector):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url)
            page.locator('#chiefAgents button[data-id="'+agent+'"]').click()
            home='#jobOverview' if agent=='jobs' else '#agentHome'
            expect(page.locator(home+' h1')).to_have_text(title)
            page.locator('#navLinks button[data-target="'+page_id+'"]').click()
            expect(page.locator('#'+page_id)).to_be_visible()
            page.locator(home_selector).click()
            expect(page.locator('#jobOverview' if agent=='jobs' else '#agentDetail')).to_be_visible()
            home='#jobOverview' if agent=='jobs' else '#agentHome'
            expect(page.locator(home+' h1')).to_have_text(title)
        finally:browser.close()



@pytest.mark.parametrize('provider,expected_url,expected_header,model_key', [
    ('groq', 'https://api.groq.com/openai/v1/models', ('Authorization', 'Bearer test-key'), 'data'),
    ('mistral', 'https://api.mistral.ai/v1/models', ('Authorization', 'Bearer test-key'), 'data'),
    ('openai', 'https://api.openai.com/v1/models', ('Authorization', 'Bearer test-key'), 'data'),
    ('anthropic', 'https://api.anthropic.com/v1/models', ('x-api-key', 'test-key'), 'data'),
    ('gemini', 'https://generativelanguage.googleapis.com/v1beta/models', ('x-goog-api-key', 'test-key'), 'models'),
    ('deepseek', 'https://api.deepseek.com/models', ('Authorization', 'Bearer test-key'), 'data'),
    ('xai', 'https://api.x.ai/v1/models', ('Authorization', 'Bearer test-key'), 'data'),
    ('openrouter', 'https://openrouter.ai/api/v1/models', ('Authorization', 'Bearer test-key'), 'data'),
])
def test_cloud_provider_adapters_request_structure(tmp_path, monkeypatch, provider, expected_url, expected_header, model_key):
    setup = ModelSetup(tmp_path)
    monkeypatch.setattr(imported, 'provision', lambda *a: 'backend')
    monkeypatch.setattr(imported, 'resolve', lambda *a: 'test-key')

    target_model = 'test-model-id'
    body = dict(provider=provider, provider_model=target_model, cost='UNKNOWN', api_key='test-key')
    record = setup.add(body, actor='owner')

    captured = {}
    class FakeResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, limit):
            if model_key == 'data':
                return json.dumps({'data': [{'id': target_model}]}).encode()
            else:
                return json.dumps({'models': [{'name': f'models/{target_model}'}]}).encode()

    def fake_open(req, timeout):
        captured['url'] = req.full_url
        captured['header'] = req.get_header(expected_header[0].capitalize())
        captured['timeout'] = timeout
        return FakeResponse()

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *handlers: SimpleNamespace(open=fake_open))

    res = setup.check_connection(record['id'])
    assert res['status'] == 'CONNECTED'
    assert res['model_listed'] is True
    assert captured['url'] == expected_url
    assert captured['header'] == expected_header[1]
    assert captured['timeout'] == 10
    if provider == 'openrouter':
        assert 'Aggregator routing restrictions' in res['message']


def test_ollama_loopback_and_key_optional(tmp_path, monkeypatch):
    setup = ModelSetup(tmp_path)
    monkeypatch.setattr(imported, 'provision', lambda *a: 'backend')

    record = setup.add(dict(provider='ollama', provider_model='llama3:latest', cost='UNKNOWN', api_key=''), actor='owner')
    assert record['key_configured'] is False

    captured = {}
    class FakeResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, limit):
            return json.dumps({'models': [{'name': 'llama3:latest'}]}).encode()

    def fake_open(req, timeout):
        captured['url'] = req.full_url
        captured['headers'] = dict(req.headers)
        return FakeResponse()

    import urllib.request
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *handlers: SimpleNamespace(open=fake_open))

    res = setup.check_connection(record['id'])
    assert res['status'] == 'CONNECTED'
    assert res['model_listed'] is True
    assert captured['url'] == 'http://127.0.0.1:11434/api/tags'


def test_ollama_ssrf_blocked_on_non_loopback(tmp_path, monkeypatch):
    setup = ModelSetup(tmp_path)
    record = setup.add(dict(provider='ollama', provider_model='llama3', cost='UNKNOWN', api_key=''), actor='owner')

    from model_registry import setup as mod
    monkeypatch.setitem(mod.ENDPOINTS, 'ollama', 'http://169.254.169.254/latest/meta-data')

    res = setup.check_connection(record['id'])
    assert res['status'] == 'CHECK_FAILED'
    assert res['code'] == 'SSRF_BLOCKED'
    assert 'loopback' in res['message']
