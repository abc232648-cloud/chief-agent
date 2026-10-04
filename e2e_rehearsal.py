from __future__ import annotations
import json, tempfile
from pathlib import Path

from database.store import Store
from database.store_extensions import init_extensions, add_candidate_fact
from policy.rules import Decision
from worker.action_gate import PolicyGate, ActionRequest, ApprovalRequired, PolicyBlocked
from worker.application_executor import ApplicationExecutor
from worker.reliable_submission import ReliableFinalSubmissionExecutor
from worker.final_submission import canonical_form_hash
from skills.source_discovery import verify_discovered_source
from skills.job_pipeline import process_job
from gateway.router import FreeOnlyGateway
from gateway.models import AIResponse
from gateway.errors import ProviderUnavailable, GatewayError

class FakeProvider:
    def __init__(self, name, response=None, unavailable=False):
        self.name, self.response, self.unavailable = name, response, unavailable
    def generate(self, request):
        if self.unavailable:
            raise ProviderUnavailable(self.name + " unavailable")
        return AIResponse(self.name, self.name + "-test", self.response or '{}')

class FakeAppBrowser:
    def __init__(self, mode): self.mode=mode; self.submit_calls=0; self.fill_calls=0
    def fill(self, url, fields):
        self.fill_calls += 1
        if self.mode == 'page_load_failed':
            return {'status':'FAILED','outcome':'FAILED_BEFORE_SUBMIT','reason':'simulated page load'}
        return {'status':'FILLED','url':url,'fields_filled':[f['label'] for f in fields],'submit':'NOT_PERFORMED'}
    def submit(self, url, selector):
        self.submit_calls += 1
        if self.mode == 'unknown':
            return {'status':'FAILED','outcome':'CONNECTION_LOST_AFTER_CLICK','reason':'simulated connection loss after click'}
        if self.mode == 'success':
            return {'status':'SUBMITTED','url_after':url+'?confirmation=1','title_after':'Application received'}
        return {'status':'FAILED','outcome':'FAILED_BEFORE_SUBMIT','reason':'simulated pre-submit failure'}

def assert_eq(name, actual, expected):
    assert actual == expected, f'{name}: expected {expected!r}, got {actual!r}'

def main():
    checks=[]
    with tempfile.TemporaryDirectory() as td:
        db=Path(td)/'worker.db'; store=Store(db); init_extensions(store)

        # 1 policy wall
        assert_eq('payment blocked', PolicyGate().decide(ActionRequest('pay_money',{})).decision, Decision.BLOCK); checks.append('policy/payment')
        assert_eq('submit asks', PolicyGate().decide(ActionRequest('submit_application',{})).decision, Decision.ASK); checks.append('policy/submit')

        # 2 source boundary
        assert_eq('http quarantine', verify_discovered_source({'name':'x','url':'http://x.example','kind':'platform'})['verification_status'], 'HTTP_QUARANTINED')
        assert_eq('https review', verify_discovered_source({'name':'x','url':'https://x.example','kind':'platform'})['verification_status'], 'REVIEW'); checks.append('source boundary')

        # 3 job pipeline + scam + IT exclusion + ranking
        prefs={'exclude_it_support':True,'remote_preferred':True}
        safe=process_job({'title':'Junior SOC Analyst','company':'Example','description':'SOC security analyst role','location':'Remote','remote':True,'compensation':'$500','source':'test','url':'https://jobs.example/soc'},prefs)
        scam=process_job({'title':'Security Analyst','company':'ScamCo','description':'Pay a fee and send money to start','remote':True,'source':'test','url':'https://scam.example/a'},prefs)
        its=process_job({'title':'IT Support Specialist','company':'Example','description':'help desk service desk','remote':True,'source':'test','url':'https://jobs.example/it'},prefs)
        assert safe['eligible'] and scam['eligible'] is False and its['eligible'] is False
        checks.append('job pipeline/scam/match')

        # 4 free-only gateway fallback and hard stop
        q=FakeProvider('qwen', unavailable=True); m=FakeProvider('mistral', response='{"ok":true}')
        gw=FreeOnlyGateway(q,m); r=gw.generate(type('R',(),{})()); assert_eq('free fallback',r.provider,'mistral'); assert r.fallback_used; checks.append('gateway/fallback')
        try:
            FreeOnlyGateway(FakeProvider('qwen',unavailable=True),FakeProvider('mistral',unavailable=True)).generate(type('R',(),{})())
        except GatewayError: checks.append('gateway/stop')
        else: raise AssertionError('gateway did not stop when both free providers failed')

        # 5 candidate fact governance + controlled fill
        fact=add_candidate_fact(store, {'text':'Wazuh experience','status':'PROPOSED','source_type':'uploaded_cv','source_id':'cv-1'})
        fields=[{'label':'Wazuh','input_type':'text','value':'Wazuh experience','fact_id':fact}]
        store.add_source({'id':'s1','name':'Approved','url':'https://approved.example','kind':'platform','verification_status':'APPROVED','confidence':1.0})
        ex=ApplicationExecutor(store,browser_factory=lambda u,f:{'status':'FILLED'})
        blocked=ex.prepare('https://approved.example/apply',fields,approved=True)
        assert_eq('unconfirmed fact blocked',blocked['status'],'BLOCKED')
        store._connect().close()
        # Explicit confirmation is the only promotion path.
        from database.store_extensions import update_candidate_fact
        update_candidate_fact(store,fact,'USER_CONFIRMED')
        filled=ex.prepare('https://approved.example/apply',fields,approved=True)
        assert_eq('confirmed fill',filled['status'],'FILLED'); checks.append('candidate facts/form fill')

        # 6 full application state through FORM_FILLED snapshot
        app='app-1'; jobid='job-1'
        store.add_job({'id':jobid,'title':'Junior SOC Analyst','company':'Example','url':'https://approved.example/job','platform':'test','remote':True,'status':'READY'})
        store.add_application(app,jobid,status='DRAFT')
        form=[{'label':'Wazuh','input_type':'text','value':'Wazuh experience','selector':'#wazuh','fact_id':fact}]
        store.add_application_snapshot({'application_id':app,'stage':'FORM_FILLED','form_fields':form,'source_url':'https://approved.example/apply'})
        expected=canonical_form_hash(form)
        assert store.application_detail(app)['snapshots']; checks.append('application archive snapshot')

        # 7 final submission requires approval and succeeds with receipt
        browser=FakeAppBrowser('success')
        final=ReliableFinalSubmissionExecutor(store,browser_factory=browser.submit)
        try:
            final.submit(app,'https://approved.example/apply','#submit',approved=False,expected_form_hash=expected)
        except ApprovalRequired: checks.append('submit approval wall')
        else: raise AssertionError('submit executed without approval')
        result=final.submit(app,'https://approved.example/apply','#submit',approved=True,expected_form_hash=expected)
        assert_eq('successful submit',result['status'],'SUBMITTED'); assert_eq('submit calls',browser.submit_calls,1); checks.append('submit/receipt')
        assert_eq('duplicate blocked',final.submit(app,'https://approved.example/apply','#submit',approved=True,expected_form_hash=expected)['status'],'BLOCKED'); checks.append('duplicate submit')

        # 8 ambiguous outcome never becomes retry
        app2='app-2'; store.add_application(app2,jobid,status='DRAFT'); store.add_application_snapshot({'application_id':app2,'stage':'FORM_FILLED','form_fields':form,'source_url':'https://approved.example/apply'})
        browser2=FakeAppBrowser('unknown'); final2=ReliableFinalSubmissionExecutor(store,browser_factory=browser2.submit)
        amb=final2.submit(app2,'https://approved.example/apply','#submit',approved=True,expected_form_hash=expected)
        assert_eq('ambiguous status',amb['status'],'FAILED'); assert_eq('ambiguous recovery',amb['recovery']['action'],'ASK'); assert_eq('ambiguous calls',browser2.submit_calls,1); checks.append('ambiguous recovery')

        # 9 pre-submit retry classification
        app3='app-3'; store.add_application(app3,jobid,status='DRAFT'); store.add_application_snapshot({'application_id':app3,'stage':'FORM_FILLED','form_fields':form,'source_url':'https://approved.example/apply'})
        browser3=FakeAppBrowser('fail'); final3=ReliableFinalSubmissionExecutor(store,browser_factory=browser3.submit)
        fail=final3.submit(app3,'https://approved.example/apply','#submit',approved=True,expected_form_hash=expected)
        assert_eq('pre-submit recovery',fail['recovery']['action'],'RETRY'); checks.append('pre-submit retry')

        # 10 audit redaction
        from database.store_extensions import add_audit, audit
        add_audit(store,'test','secret redaction',data={'password':'secret','nested':{'token':'abc','safe':'ok'}})
        row=audit(store,1)[0]; blob=row['data_json']; assert 'secret' not in blob and 'abc' not in blob and 'ok' in blob; checks.append('audit redaction')

    print(json.dumps({'status':'PASS','checks':checks,'count':len(checks)},indent=2))

if __name__=='__main__': main()
