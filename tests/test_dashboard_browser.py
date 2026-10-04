from browser_navigation import navigate
"""Actual Chromium/HTTP/SQLite tests. External AI and submission are test doubles."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from playwright.sync_api import sync_playwright, expect


def test_complete_dashboard_browser_pass(dashboard,monkeypatch):
    from worker.command_processor import CommandProcessor
    from worker.runner import run_once,run_approved_once
    from worker.reliable_submission import ReliableFinalSubmissionExecutor
    store=dashboard.store
    store.add_job({'id':'test-job','title':'SOC <img src=x onerror=alert(1)>','company':'Test Company','url':'https://example.test/job','platform':'test'})
    store.add_application('test-app','test-job',notes='Test archive')
    store.add_application_snapshot({'application_id':'test-app','cv_name':'Historical CV','cv_snapshot':{'skills':['Verified test skill']},'cover_letter_text':'Test cover letter','source_url':'https://example.test/apply'})
    store.add_application_event('test-app','DRAFT_CREATED',details='Browser test')
    store.add_notification('Test notification','Test body')
    store.add_report('test-report','/test/report.txt')
    fact=store.add_candidate_fact({'text':'Test candidate claim','status':'PROPOSED'})
    reject=store.add_action('Reject fixture','fill_application_form')
    approve=store.add_action('Approve fixture','fill_application_form',payload={'url':'https://example.test/apply','fields':[]})
    store.add_submission_attempt('test-app','SUBMIT','FAILED_BEFORE_SUBMIT',data={'url':'https://example.test/apply','submit_selector':'#submit'})
    submissions=[]
    def fake_submit(self,*args,**kwargs):
        submissions.append((args,kwargs)); return {'status':'SUBMITTED','recovery':{}}
    monkeypatch.setattr(ReliableFinalSubmissionExecutor,'submit',fake_submit)
    executed=[]
    class Executor:
        def prepare(self,*args,**kwargs):executed.append((args,kwargs));return {'status':'FILLED'}
    class Gateway:
        def generate(self,req):return SimpleNamespace(text=json.dumps({'summary':'Browser integration','actions':[]}))
    processor=CommandProcessor(store,Gateway(),application_executor=Executor())
    evidence=Path(os.environ.get('DASHBOARD_EVIDENCE_DIR',str(dashboard.app.ROOT/'evidence')))
    evidence.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':1280,'height':800})
        context.tracing.start(screenshots=True,snapshots=True,sources=False)
        page=context.new_page()
        errors=[];console=[];failed=[];bad_responses=[];dialogs=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('console',lambda m:console.append(m.text) if m.type=='error' else None)
        page.on('requestfailed',lambda r:failed.append(r.url))
        page.on('response',lambda r:bad_responses.append((r.url,r.status)) if r.status>=400 else None)
        page.on('dialog',lambda d:(dialogs.append(d.message),d.accept()))
        page.goto(dashboard.url)
        navigate(page,'jobOverview')
        expect(page.locator('#jobsCount')).to_have_text('1')
        expect(page.locator('#apps')).to_have_text('1')
        expect(page.locator('#actionsCount')).to_have_text('2')
        expect(page.locator('#notifs')).to_have_text('1')
        expect(page.locator('#dashboardError')).to_be_hidden()
        targets=['overview','domains','actions','notifications','reports','audit','scheduler','settings','ai','agentDetail','jobOverview','jobs','applications','applicationArchive','sources','cvs','profiles','facts']
        for target in targets:
            navigate(page,target)
            expect(page.locator(f'section#{target}')).to_be_visible()
            expect(page.locator('main section:not(.hidden)')).to_have_count(1)
            expect(page.locator(f'section#{target}')).not_to_contain_text('Loading…')
        assert len(targets)==18
        # Fixed desktop navigation must scroll on short windows, and stack on mobile.
        page.set_viewport_size({'width':1280,'height':500})
        navigate(page,'settings')
        expect(page.locator('#emailConfig')).to_contain_text('Password')
        assert page.locator('nav').evaluate('(e)=>getComputedStyle(e).overflowY')=='auto'
        page.set_viewport_size({'width':390,'height':844})
        navigate(page,'jobs')
        expect(page.locator('#jobList')).to_contain_text('SOC <img')
        assert page.locator('#jobList img').count()==0 and dialogs==[]
        page.set_viewport_size({'width':1280,'height':800})
        navigate(page,'jobOverview')
        page.locator('#cmd').fill('Controlled browser command')
        page.locator('[data-action="send-command"]').click()
        expect(page.locator('#cmdResult')).to_contain_text('QUEUED')
        assert run_once(processor) is True
        navigate(page,'jobOverview')
        expect(page.locator('#commands')).to_contain_text('No action was selected')
        navigate(page,'actions')
        page.locator(f'[data-action="resolve-action"][data-id="{reject}"][data-status="REJECTED"]').click()
        expect(page.locator('#actionList')).not_to_contain_text('Reject fixture')
        assert store.get_action(reject)['status']=='REJECTED'
        page.locator(f'[data-action="resolve-action"][data-id="{approve}"][data-status="APPROVED"]').click()
        expect(page.locator('#actionList')).to_contain_text('No pending actions')
        assert run_approved_once(processor) is True and len(executed)==1
        assert store.get_action(approve)['status']=='DONE'
        navigate(page,'applications')
        page.locator('[data-action="show-application"]').first.click()
        expect(page.locator('#applicationDetail')).to_contain_text('Historical CV')
        expect(page.locator('#applicationDetail')).to_contain_text('Test cover letter')
        expect(page.locator('#applicationDetail')).to_contain_text('DRAFT_CREATED')
        page.locator('[data-action="retry-application"]').click()
        expect(page.locator('[data-action="retry-application"]')).to_be_enabled()
        assert len(submissions)==1 and len(dialogs)==1 and '\nStatus:' in dialogs[0]
        navigate(page,'cvs')
        page.locator('[data-action="upload-cv"]').click()
        expect(page.locator('#cvResult')).to_have_text('Choose a file first.')
        page.locator('#cvName').fill('Browser test CV')
        page.locator('#cvFile').set_input_files({'name':'test-cv.txt','mimeType':'text/plain','buffer':b'Test CV; no real candidate information'})
        page.locator('[data-action="upload-cv"]').click()
        expect(page.locator('#cvResult')).to_contain_text('UPLOADED')
        expect(page.locator('#cvList')).to_contain_text('Browser test CV')
        page.locator('[data-action="toggle-cv"]').click()
        expect(page.locator('#cvList')).to_contain_text('Inactive')
        page.locator('[data-action="toggle-cv"]').click()
        expect(page.locator('#cvList')).not_to_contain_text('Inactive')
        navigate(page,'facts')
        page.locator(f'[data-action="fact-status"][data-id="{fact}"][data-status="USER_CONFIRMED"]').click()
        expect(page.locator('#factList')).to_contain_text('USER CONFIRMED')
        page.locator(f'[data-action="fact-status"][data-id="{fact}"][data-status="REVOKED"]').click()
        expect(page.locator('#factList')).to_contain_text('REVOKED')
        navigate(page,'profiles')
        page.locator('#profileLabel').fill('Browser profile')
        page.locator('#profileUrl').fill('https://example.test/profile')
        page.locator('[data-action="add-profile"]').click()
        expect(page.locator('#profileResult')).to_contain_text('ADDED')
        page.locator('[data-action="toggle-profile"]').click()
        expect(page.locator('#profileList')).to_contain_text('Disabled')
        page.locator('[data-action="toggle-profile"]').click()
        expect(page.locator('#profileList')).to_contain_text('Enabled')
        page.locator('[data-action="delete-profile"]').click()
        expect(page.locator('#profileList')).to_contain_text('No profile links')
        navigate(page,'audit')
        page.locator('[data-action="load-audit"]').click()
        expect(page.locator('#auditText')).to_contain_text('Controlled browser command')
        page.locator('#audit [data-action="generate-audit"]').click()
        expect(page.locator('#auditText')).to_contain_text('.txt')
        navigate(page,'jobOverview')
        before=store.counts()['actions']
        page.wait_for_timeout(5200) # Exercise the real periodic refresh.
        expect(page.locator('#actionsCount')).to_have_text(str(before))
        expect(page.locator('#dashboardError')).to_be_hidden()
        page.screenshot(path=str(evidence/'overview.png'),full_page=True)
        assert not errors and not console and not failed and not bad_responses, (errors,console,failed,bad_responses)
        (evidence/'browser-result.json').write_text(json.dumps({'status':'PASS','navigation_pages':targets,'page_errors':errors,'console_errors':console,'failed_requests':failed,'http_errors':bad_responses,'controls':['command','approve','reject','application archive','retry','upload CV','toggle CV','confirm fact','revoke fact','add profile','toggle profile','remove profile','refresh audit','generate audit'],'external_services':'test doubles; no real applications submitted'},indent=2))
        context.tracing.stop(path=str(evidence/'browser-trace.zip'))
        browser.close()


def test_browser_handles_failed_and_malformed_api_without_losing_command(dashboard):
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page()
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(dashboard.url)
        navigate(page,'jobOverview')
        expect(page.locator('#jobsCount')).to_have_text('0')
        page.route('**/api/jobs',lambda r:r.fulfill(status=500,content_type='application/json',body='{"reason":"Simulated API failure"}'))
        navigate(page,'jobs')
        expect(page.locator('#dashboardError')).to_have_text('Simulated API failure')
        page.unroute('**/api/jobs')
        navigate(page,'jobs')
        expect(page.locator('#dashboardError')).to_be_hidden()
        expect(page.locator('#jobList')).to_have_text('No jobs yet.')
        page.route('**/api/settings/email',lambda r:r.fulfill(status=200,content_type='text/html',body='<html>Not JSON</html>'))
        navigate(page,'settings')
        expect(page.locator('#dashboardError')).to_contain_text('Invalid server response')
        page.route('**/api/command',lambda r:r.abort())
        navigate(page,'jobOverview')
        page.locator('#cmd').fill('Keep this failed command')
        page.locator('[data-action="send-command"]').click()
        expect(page.locator('#dashboardError')).to_be_visible()
        expect(page.locator('#cmd')).to_have_value('Keep this failed command')
        assert not errors
        browser.close()
