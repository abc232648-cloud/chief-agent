from __future__ import annotations
import os
from pathlib import Path
from notifications.center import NotificationCenter
from notifications.channels import LocalInboxChannel
from notifications.models import ActionItem, PeriodSummary
from notifications.daily_full_audit import send_daily_full_audit
from database.store import Store
from database.store_extensions import init_extensions

class BrokenChannel:
    def send_notification(self, notification): raise RuntimeError('email down')
    def send_summary(self, summary): raise RuntimeError('email down')

def test_notification_channel_failure_does_not_block_local(tmp_path):
    inbox=tmp_path/'outbox'
    center=NotificationCenter([BrokenChannel(), LocalInboxChannel(inbox)])
    n=center.action_required('Action','Please approve', [ActionItem('Approve','x','high')], urgent=True)
    assert n.severity.value == 'URGENT'
    assert list(inbox.glob('action_*.txt'))

def test_daily_audit_uses_v26_email_names_and_keeps_txt_when_unconfigured(tmp_path, monkeypatch):
    store=Store(tmp_path/'db.sqlite'); init_extensions(store)
    for k in ['SMTP_HOST','SMTP_USERNAME','SMTP_PASSWORD','SMTP_RECIPIENT','SMTP_SENDER']:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('EMAIL_PROVIDER','custom_smtp')
    monkeypatch.setenv('EMAIL_RECIPIENT','')
    result=send_daily_full_audit(store, outbox=tmp_path/'notifications'/'outbox')
    assert result['email_status']=='NOT_CONFIGURED'
    assert Path(result['txt_path']).exists()

def test_approved_action_recovered_after_crash(tmp_path):
    store=Store(tmp_path/'db.sqlite'); init_extensions(store)
    aid=store.add_action('Submit application', 'submit_application', payload={'x':1})
    store.resolve_action(aid, 'APPROVED')
    claimed=store.next_approved_action()
    assert claimed and claimed['id']==aid and claimed['status']=='EXECUTING'
    store.reset_executing_actions()
    assert store.get_action(aid)['status']=='REVIEW'
    assert store.next_approved_action() is None

def test_final_submit_form_hash_mismatch_blocks(tmp_path):
    from worker.final_submission import FinalSubmissionExecutor, canonical_form_hash
    store=Store(tmp_path/'db.sqlite'); init_extensions(store)
    store.add_source({'id':'s','name':'Approved','url':'https://approved.example','kind':'platform','verification_status':'APPROVED'})
    store.add_job({'id':'j','title':'SOC','company':'C','url':'https://approved.example/j','platform':'x','remote':True,'status':'READY'})
    store.add_application('a','j')
    fields=[{'label':'Name','input_type':'text','value':'Ab','selector':'#name','fact_id':1}]
    store.add_application_snapshot({'application_id':'a','stage':'FORM_FILLED','form_fields':fields,'source_url':'https://approved.example/apply'})
    wrong='0'*64
    r=FinalSubmissionExecutor(store,browser_factory=lambda u,s: (_ for _ in ()).throw(AssertionError('browser must not run'))).submit('a','https://approved.example/apply','#submit',approved=True,expected_form_hash=wrong)
    assert r['status']=='BLOCKED'
