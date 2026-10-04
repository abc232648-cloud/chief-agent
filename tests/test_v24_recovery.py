import json
from database.store import Store
from worker.recovery import RecoveryManager


def setup(tmp_path):
    s=Store(tmp_path/'db.sqlite')
    s.add_job({'id':'j1','title':'SOC Analyst','company':'Acme','url':'https://acme.example/jobs/1'})
    s.add_application('a1','j1')
    return s


def test_ambiguous_submission_never_auto_retries(tmp_path):
    s=setup(tmp_path); r=RecoveryManager(s,max_retries=2)
    aid=r.begin('a1','FINAL_SUBMISSION','abc'); r.finish(aid,'SUBMISSION_UNKNOWN',details='timeout')
    d=r.decide('a1','SUBMISSION_UNKNOWN')
    assert d.action=='ASK'
    r.mark_recovery('a1',d)
    assert s.application_detail('a1')['recovery_status']=='ASK'


def test_pre_submit_failure_can_retry_bounded(tmp_path):
    s=setup(tmp_path); r=RecoveryManager(s,max_retries=2)
    a=r.begin('a1','NAVIGATION'); r.finish(a,'FAILED_BEFORE_SUBMIT')
    d=r.decide('a1','FAILED_BEFORE_SUBMIT')
    assert d.action=='RETRY'


def test_retry_limit_stops(tmp_path):
    s=setup(tmp_path); r=RecoveryManager(s,max_retries=1)
    for _ in range(2):
        a=r.begin('a1','NAVIGATION'); r.finish(a,'FAILED_BEFORE_SUBMIT')
    d=r.decide('a1','FAILED_BEFORE_SUBMIT')
    assert d.action=='ASK'


def test_attempts_are_persisted(tmp_path):
    s=setup(tmp_path); r=RecoveryManager(s)
    a=r.begin('a1','FINAL_SUBMISSION','hash'); r.finish(a,'SUBMITTED',data={'receipt':'x'})
    row=s.submission_attempt(a)
    assert row['form_hash']=='hash' and row['outcome']=='SUBMITTED'

def test_retry_request_is_available_for_known_pre_submit_failure(tmp_path):
    s=setup(tmp_path)
    r=RecoveryManager(s,max_retries=2)
    a=r.begin('a1','NAVIGATION')
    r.finish(a,'FAILED_BEFORE_SUBMIT',details='temporary navigation failure',data={
        'url':'https://acme.example/apply/1','submit_selector':'#submit','expected_form_hash':'hash'
    })
    latest=s.latest_submission_attempt('a1')
    d=r.decide('a1',latest['outcome'])
    assert d.action=='RETRY'
    payload=json.loads(latest['data_json'])
    assert payload['url'].startswith('https://') and payload['submit_selector']=='#submit'


def test_ambiguous_record_has_no_retry_decision(tmp_path):
    s=setup(tmp_path)
    r=RecoveryManager(s,max_retries=2)
    a=r.begin('a1','FINAL_SUBMISSION')
    r.finish(a,'SUBMISSION_UNKNOWN',details='connection lost',data={'url':'https://acme.example/apply/1','submit_selector':'#submit'})
    d=r.decide('a1','SUBMISSION_UNKNOWN')
    assert d.action=='ASK'
