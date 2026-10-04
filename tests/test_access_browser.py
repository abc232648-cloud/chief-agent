from browser_navigation import navigate
"""Real Chromium against intercepted fixture websites; no real credentials/submissions."""
import json
import os
import subprocess
from types import SimpleNamespace
import pytest
from playwright.sync_api import sync_playwright, expect
from browser.site_access import SiteAccess
from database.store import Store


def assert_private_session(path):
    if os.name != 'nt':
        assert path.stat().st_mode & 0o777 == 0o600
        return
    # Windows mode bits do not express NTFS access control. Inspect actual
    # allowed trustees, including inherited ACEs, without reading the secret.
    script = """
    $ErrorActionPreference='Stop'
    $acl=[System.IO.File]::GetAccessControl($env:CHIEF_TEST_ACL_PATH)
    $sidType=[System.Security.Principal.SecurityIdentifier]
    [Console]::WriteLine($acl.GetOwner($sidType).Value)
    foreach($rule in $acl.GetAccessRules($true,$true,$sidType)) {
      [Console]::WriteLine($rule.IdentityReference.Value+'|'+$rule.AccessControlType.ToString()+'|'+[int64]$rule.FileSystemRights)
    }
    """
    acl_env={**os.environ,'CHIEF_TEST_ACL_PATH':str(path)}
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
        env=acl_env,capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    lines=result.stdout.strip().splitlines()
    acl={'owner':lines[0],'rules':[]}
    for line in lines[1:]:
        sid,kind,rights=line.split('|')
        acl['rules'].append({'sid':sid,'type':kind,'rights':int(rights)})
    allowed={acl['owner'],'S-1-5-18','S-1-5-32-544','S-1-3-4'}  # owner, SYSTEM, administrators, OWNER RIGHTS
    grants=[r for r in acl['rules'] if r['type']=='Allow']
    assert grants,'A private session must have an explicit or inherited access policy.'
    assert all(r['sid'] in allowed for r in grants),acl
    assert any(r['sid'] in {acl['owner'],'S-1-3-4'} and r['rights'] & 1 for r in grants),acl


def test_sources_controls_in_actual_dashboard(dashboard):
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page()
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('dialog',lambda d:d.accept())
        page.goto(dashboard.url)
        navigate(page,'sources')
        page.locator('#siteName').fill('Fixture platform')
        page.locator('#siteUrl').fill('https://fixture.example/jobs')
        page.locator('[data-action="add-site"]').click()
        expect(page.locator('#sourceDetail')).to_contain_text('DISABLED')
        page.locator('[data-operation="public"]').click()
        expect(page.locator('#sourceDetail')).to_contain_text('ACTIVE')
        page.locator('[data-operation="suspend"]').click()
        expect(page.locator('#sourceDetail')).to_contain_text('PAUSED')
        assert SiteAccess(dashboard.store).get('fixture.example')['resume_at'] is not None
        page.locator('[data-operation="resume"]').click()
        page.locator('[data-operation="pause"]').click()
        expect(page.locator('#sourceDetail')).to_contain_text('PAUSED')
        assert SiteAccess(dashboard.store).get('fixture.example')['resume_at'] is None
        page.locator('[data-operation="revoke"]').click()
        expect(page.locator('#sourceDetail')).to_contain_text('REVOKED')
        page.locator('[data-operation="dismiss"]').click()
        expect(page.locator('#sourceDetail')).to_contain_text('Recommendation dismissed')
        page.reload();navigate(page,'sources')
        page.get_by_role('button',name='Fixture platform',exact=True).click()
        expect(page.locator('#sourceDetail')).to_contain_text('REVOKED')
        assert not errors
        browser.close()


def intercept_chromium(monkeypatch, html, after_goto=None):
    import playwright.sync_api as api
    real=sync_playwright
    class Factory:
        def __enter__(self):
            self.cm=real();p=self.cm.__enter__()
            def launch(**kwargs):
                browser=p.chromium.launch(headless=True)
                new_context=browser.new_context
                def context(**options):
                    ctx=new_context(**options)
                    ctx.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=html))
                    if after_goto:
                        new_page=ctx.new_page
                        def page():
                            pg=new_page();goto=pg.goto
                            def visit(*a,**kw):
                                result=goto(*a,**kw);after_goto(pg,ctx);return result
                            pg.goto=visit
                            return pg
                        ctx.new_page=page
                    return ctx
                browser.new_context=context
                return browser
            return SimpleNamespace(chromium=SimpleNamespace(launch=launch))
        def __exit__(self,*args):return self.cm.__exit__(*args)
    monkeypatch.setattr(api,'sync_playwright',Factory)


def test_browser_login_saves_scoped_session_and_reader_reuses_it(tmp_path,monkeypatch):
    from browser.site_login import login
    from browser.playwright_reader import PlaywrightReader,BrowserReadConfig
    access=SiteAccess(Store(tmp_path/'db'))
    access.add('https://fixture.example/jobs')
    row=access.change('fixture.example','login')
    def signed_in(page,context):
        # A fake login represents the user's actions, with no real password.
        page.locator('#name').fill('fixture-user')
        page.locator('#password').fill('fixture-password')
        page.locator('#login').click()
        context.add_cookies([{'name':'other','value':'must-not-save','domain':'other.example','path':'/'}])
        access.change('fixture.example','save')
    intercept_chromium(monkeypatch,'<input id="name"><input id="password" type="password"><button id="login" onclick="document.cookie=\'session=fixture; Secure; path=/\'">Sign in</button>',signed_in)
    login(str(access.store.path),'fixture.example',row['revision'])
    state=access.state('fixture.example')
    assert [c['name'] for c in state['cookies']]==['session']
    assert_private_session(access.path('fixture.example'))
    intercept_chromium(monkeypatch,'<title>Fixture jobs</title><body><script>document.write(document.cookie.includes("session=fixture")?"Signed-in jobs":"Sign in required")</script></body>')
    reader=PlaywrightReader(BrowserReadConfig(allowed_domains=('fixture.example',)),access=access,domain='fixture.example')
    listing=reader.read_url('https://fixture.example/jobs')
    assert 'Signed-in jobs' in listing.payload['text']


def test_expired_session_is_visible_and_stops_reading(tmp_path,monkeypatch):
    from browser.playwright_reader import PlaywrightReader,BrowserReadConfig,BrowserReadError
    access=SiteAccess(Store(tmp_path/'db'));access.add('https://fixture.example/jobs');access.change('fixture.example','public')
    intercept_chromium(monkeypatch,'<body>Please sign in<input type="password"></body>')
    reader=PlaywrightReader(BrowserReadConfig(allowed_domains=('fixture.example',)),access=access,domain='fixture.example')
    with pytest.raises(BrowserReadError,match='Sign-in required'):reader.read_url('https://fixture.example/jobs')
    assert access.get('fixture.example')['status']=='LOGIN_REQUIRED'


@pytest.mark.parametrize('confirmed',[True,False])
def test_actual_submission_checks_fields_and_confirmation(tmp_path,monkeypatch,confirmed):
    from worker.final_submission import FinalSubmissionExecutor,canonical_form_hash
    store=Store(tmp_path/'db')
    store.add_source({'id':'fixture','name':'Fixture','url':'https://fixture.example','protocol':'HTTPS','verification_status':'APPROVED'})
    store.add_job({'id':'job','title':'SOC Analyst','url':'https://fixture.example/job'})
    store.add_application('app','job')
    fid=store.add_candidate_fact({'text':'Email: fixture@example.test','status':'USER_CONFIRMED'})
    fields=[{'selector':'#email','label':'Email','input_type':'email','value':'fixture@example.test','fact_id':fid}]
    store.add_application_snapshot({'application_id':'app','stage':'FORM_FILLED','source_url':'https://fixture.example/apply','form_fields':fields})
    message='Your application has been submitted' if confirmed else 'Still processing'
    html='<input id="email" type="email"><button id="submit" onclick="if(document.querySelector(\'#email\').value===\'fixture@example.test\')document.body.innerHTML=\''+message+'\'">Submit application</button>'
    intercept_chromium(monkeypatch,html)
    executor=FinalSubmissionExecutor(store)
    result=executor.submit('app','https://fixture.example/apply','#submit',approved=True,expected_form_hash=canonical_form_hash(fields))
    assert result['status']==('SUBMITTED' if confirmed else 'REVIEW')
    assert store.application_detail('app')['status']==('SUBMITTED' if confirmed else 'SUBMISSION_UNKNOWN')
    assert not executor.preflight('app','https://fixture.example/apply').ok
