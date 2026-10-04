from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
import json
import pytest
from database.store import Store
from worker.command_processor import CommandProcessor
from worker.approval_context import approval_scope,revalidate
from tests.checkpoint_f_fixture import secure_store
from identity.service import IdentityService
from operations.time_integrity import utc_now,utc_text


def action(store):
    return store.add_action('Synthetic approved fill','fill_application_form',payload={'url':'https://example.test/apply','fields':[]})


@pytest.mark.parametrize('claimed',[False,True])
def test_concurrent_approved_calls_reach_sink_only_once(tmp_path,claimed):
    store=Store(tmp_path/'db.sqlite');aid=action(store);store.resolve_action(aid,'APPROVED')
    if claimed:assert store.next_approved_action()['id']==aid
    entered=Event();release=Event();calls=[]
    class Executor:
        def prepare(self,*a,**kw):
            calls.append(1);entered.set();assert release.wait(10);return {'status':'FILLED'}
    processor=CommandProcessor(store,None,application_executor=Executor())
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(processor.process_approved_action,aid,claimed=claimed)
        try:
            assert entered.wait(10)
            assert processor.process_approved_action(aid,claimed=claimed)['status']=='BLOCKED'
            assert store.get_action(aid)['status']=='EXECUTING'
        finally:release.set()
        assert first.result()['status']=='FILLED'
    assert calls==[1] and store.get_action(aid)['status']=='DONE'
    assert processor.process_approved_action(aid,claimed=claimed)['status']=='FAILED'
    assert calls==[1]


@pytest.mark.parametrize('change',['revoked','expired','future','payload','action','disabled'])
def test_changed_identified_approval_never_reaches_executor(tmp_path,monkeypatch,change):
    store=Store(tmp_path/'db.sqlite');raw,human=secure_store(store)
    service=IdentityService(store);aid=action(store);service.approve_action(human,aid,'jobs')
    if change in {'expired','future'}:
        observed=utc_now()+timedelta(minutes=31 if change=='expired' else -5)
        original=IdentityService.__init__
        monkeypatch.setattr(IdentityService,'__init__',lambda self,store,**kw:original(self,store,now=lambda:observed))
    with store._connect() as con:
        if change=='revoked':con.execute('UPDATE human_sessions SET revoked=1 WHERE id=?',(human.session_id,))
        elif change in {'expired','future'}:pass
        elif change=='payload':con.execute('UPDATE actions SET payload_json=? WHERE id=?',(json.dumps({'url':'https://other.test'}),aid))
        elif change=='action':con.execute("UPDATE actions SET action='submit_application' WHERE id=?",(aid,))
        else:con.execute('UPDATE human_identities SET enabled=0 WHERE id=?',(human.id,))
    class Executor:
        def prepare(self,*a,**kw):pytest.fail('Invalid approval reached an effect sink')
    assert CommandProcessor(store,None,application_executor=Executor()).process_approved_action(aid)['status']=='BLOCKED'
    assert store.get_action(aid)['status']=='BLOCKED'


def test_revocation_during_prepare_is_rechecked_and_no_effect(tmp_path):
    store=Store(tmp_path/'db.sqlite');_,human=secure_store(store)
    service=IdentityService(store);aid=action(store);service.approve_action(human,aid,'jobs');calls=[]
    class Executor:
        def prepare(self,*a,**kw):
            service.revoke(human);revalidate();calls.append('effect');return {'status':'FILLED'}
    assert CommandProcessor(store,None,application_executor=Executor()).process_approved_action(aid)['status']=='FAILED'
    assert calls==[]


def test_context_never_leaks_and_positive_approval_still_works(tmp_path):
    store=Store(tmp_path/'db.sqlite');_,human=secure_store(store)
    aid=action(store);IdentityService(store).approve_action(human,aid,'jobs');calls=[]
    class Executor:
        def prepare(self,*a,**kw):revalidate();calls.append('effect');return {'status':'FILLED'}
    assert CommandProcessor(store,None,application_executor=Executor()).process_approved_action(aid)['status']=='FILLED'
    assert calls==['effect'];revalidate()
    with pytest.raises(PermissionError):
        with approval_scope(lambda:(_ for _ in ()).throw(PermissionError())):revalidate()
    revalidate()


def test_browser_seam_checks_live_approval_before_effect(tmp_path):
    from worker.application_executor import ApplicationExecutor
    store=Store(tmp_path/'db.sqlite');calls=[]
    store.add_source({'id':'fixture','name':'Fixture','url':'https://example.test','protocol':'HTTPS','verification_status':'APPROVED'})
    executor=ApplicationExecutor(store,browser_factory=lambda *a:calls.append(1))
    def deny():raise PermissionError('Approval revoked')
    with approval_scope(deny),pytest.raises(PermissionError):executor.prepare('https://example.test/apply',[],approved=True)
    assert calls==[]


@pytest.mark.parametrize('operation',['fill','submit'])
def test_real_browser_revocation_after_navigation_prevents_mutation(tmp_path,monkeypatch,operation):
    from tests.test_access_browser import intercept_chromium
    from worker.application_executor import ApplicationExecutor
    from worker.final_submission import FinalSubmissionExecutor,canonical_form_hash
    store=Store(tmp_path/'db.sqlite');_,human=secure_store(store)
    store.add_source({'id':'fixture','name':'Fixture','url':'https://fixture.example','protocol':'HTTPS','verification_status':'APPROVED'})
    store.add_job({'id':'job','title':'Synthetic','url':'https://fixture.example/job'});store.add_application('app','job')
    fid=store.add_candidate_fact({'text':'Email: fixture@example.test','status':'USER_CONFIRMED'})
    fields=[{'selector':'#email','label':'Email','input_type':'email','value':'fixture@example.test','fact_id':fid}]
    store.add_application_snapshot({'application_id':'app','stage':'FORM_FILLED','source_url':'https://fixture.example/apply','form_fields':fields})
    values=[];clicked=[];pages=[]
    from playwright.sync_api import Browser
    close_browser=Browser.close
    def checked_close(browser,*args,**kwargs):
        for page in pages:
            if not page.is_closed():values.append(page.locator('#email').input_value())
        return close_browser(browser,*args,**kwargs)
    monkeypatch.setattr(Browser,'close',checked_close)
    def revoke(page,context):
        IdentityService(store).revoke(human)
        page.on('console',lambda message:clicked.append(message.text))
        pages.append(page)
    intercept_chromium(monkeypatch,'<input id="email" type="email"><button id="submit" onclick="console.log(\'UNSAFE_CLICK\')">Submit application</button>',revoke)
    payload={'url':'https://fixture.example/apply','fields':fields,'application_id':'app','submit_selector':'#submit','expected_form_hash':canonical_form_hash(fields)}
    aid=store.add_action('Synthetic browser test','fill_application_form' if operation=='fill' else 'submit_application',payload=payload)
    IdentityService(store).approve_action(human,aid,'jobs')
    result=CommandProcessor(store,None).process_approved_action(aid)
    assert result['status'] in {'BLOCKED','FAILED','REVIEW'}
    assert values and all(value=='' for value in values)
    assert 'UNSAFE_CLICK' not in clicked
    assert store.application_detail('app')['status'] not in {'SUBMITTING','SUBMITTED'}


def test_changed_component_authority_stops_sink(tmp_path):
    from security.permissions import worker_context
    from types import SimpleNamespace
    store=Store(tmp_path/'db.sqlite');aid=action(store);store.resolve_action(aid,'APPROVED');calls=[]
    agent=SimpleNamespace(id='jobs',domain='jobs',capabilities=frozenset({'jobs.execute'}))
    class Executor:
        def prepare(self,*a,**kw):revalidate();calls.append(1);return {'status':'FILLED'}
    with worker_context(agent,allowed=lambda domain:False):
        assert CommandProcessor(store,None,application_executor=Executor()).process_approved_action(aid)['status']=='BLOCKED'
    assert calls==[]


def test_changed_domain_ownership_stops_effect(tmp_path):
    store=Store(tmp_path/'db.sqlite');_,human=secure_store(store)
    cid=store.queue_command('Synthetic');aid=store.add_action('Synthetic','fill_application_form',payload={'command_id':cid,'payload':{'url':'https://example.test','fields':[]}})
    IdentityService(store).approve_action(human,aid,'jobs');calls=[]
    class Executor:
        def prepare(self,*a,**kw):
            with store._connect() as con:con.execute("INSERT INTO domain_requests(command_id,domain,action,payload_json) VALUES(?,'farming','fixture','{}')",(cid,))
            revalidate();calls.append(1);return {'status':'FILLED'}
    assert CommandProcessor(store,None,application_executor=Executor()).process_approved_action(aid)['status']=='FAILED'
    assert calls==[]
