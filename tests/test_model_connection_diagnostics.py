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


def test_job_home_has_workspace_links_without_reopening_itself(dashboard):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url)
            page.locator('#chiefAgents button[data-id="jobs"]').click()
            expect(page.locator('#agentHome h1')).to_have_text('Job Agent')
            assert page.locator('#agentHome button[data-action="open-agent"]').count()==0
            assert page.locator('#agentHome button[data-action="show"]').count()>0
        finally:browser.close()


@pytest.mark.parametrize('agent,page_id,title',[('jobs','sources','Job Agent'),('farming','farmRecords','Farm Agent')])
@pytest.mark.parametrize('home_selector',['#workspaceName','button.brand'])
def test_agent_header_and_brand_return_to_agent_home(dashboard,agent,page_id,title,home_selector):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url)
            page.locator('#chiefAgents button[data-id="'+agent+'"]').click()
            expect(page.locator('#agentHome h1')).to_have_text(title)
            page.locator('#navLinks button[data-target="'+page_id+'"]').click()
            expect(page.locator('#'+page_id)).to_be_visible()
            page.locator(home_selector).click()
            expect(page.locator('#agentDetail')).to_be_visible()
            expect(page.locator('#agentHome h1')).to_have_text(title)
        finally:browser.close()
