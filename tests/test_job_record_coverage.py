import json
from domains.jobs.record_coverage import summarize
from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_identity_http import request


def statuses(result):return {r['key']:r['status'] for r in result['items']}


def test_missing_is_unknown_and_status_does_not_prove_delivery():
    result=summarize({'id':'a','status':'SUBMITTED','cv_path':'a-file.docx'},[])
    assert set(statuses(result).values())=={'NOT_RECORDED'}
    assert result['authority']=='NONE'


def test_snapshot_presence_and_revocation_never_grant_authority():
    detail={'id':'a','draft_json':json.dumps({'claims':[{'fact_id':1,'text':'Python'}]}),'snapshots':[{'cv_snapshot_json':'{}'},{'cv_snapshot_json':'{"skills":["Python"]}','cover_letter_text':'Letter','source_url':'https://example.test'}],'submission_receipt_json':'[]'}
    facts=[{'id':1,'status':'USER_CONFIRMED','text':'Python'}]
    assert statuses(summarize(detail,facts))['claims']=='REFERENCES_MATCH'
    facts[0]['status']='REVOKED'
    result=summarize(detail,facts)
    assert statuses(result)['claims']=='REVIEW_REQUIRED'
    assert statuses(result)['receipt']=='NOT_RECORDED'
    assert statuses(result)['cv']=='RECORDED'
    assert result['authority']=='NONE'


def test_http_privacy_read_only_and_browser_coverage(dashboard):
    from playwright.sync_api import sync_playwright,expect
    d=dashboard;d.store.add_job({'id':'coverage-job','title':'Synthetic role','company':'Synthetic employer'})
    d.store.add_application('coverage-app','coverage-job')
    before=d.store.application_detail('coverage-app')
    path='/api/applications/coverage-app/coverage'
    assert request(d,path)[0]==401
    assert request(d,path,raw=d.credentials['raw'])[0]==200
    svc=IdentityService(d.store);svc.create_user(d.credentials['principal'],'farm-coverage-worker',PASSWORD,'Worker',('farming',));raw,_=svc.login('farm-coverage-worker',PASSWORD)
    assert request(d,path,raw=raw)[0]==403
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(d.url)
            page.evaluate("showApplication('coverage-app')")
            expect(page.locator('#applicationDetail')).to_contain_text('Application record coverage')
            expect(page.locator('#applicationDetail')).to_contain_text('Not recorded')
        finally:browser.close()
    assert before==d.store.application_detail('coverage-app')
