from application.composition import default_registry
import base64
import io
import json
import os
from pathlib import Path
import time
import zipfile
import pytest
from test_dashboard_runtime import request
from control.agents import AgentControls
from domains.runtime import DomainRuntime
from worker.runner import run_once
from database.store import Store


def test_paused_agent_does_not_claim_work_and_autostart_is_independent(tmp_path):
    runtime=DomainRuntime(Store(tmp_path/'db'), registry=default_registry());c=runtime.controls
    c.change('farming',{'autostart':False});c.startup()
    queued=runtime.queue('farming','record_soil_test',{'plot':'A','sample_date':'2026-01-01','ph':6,'source':'fixture','confirmed':True})
    assert run_once(runtime) is False
    assert runtime.store.commands()[0]['status']=='QUEUED'
    c.change('farming',{'running':True});assert run_once(runtime)
    assert runtime.store.commands()[0]['status']=='COMPLETED'
    c.change('farming',{'enabled':False,'autostart':True});c.startup()
    assert not c.allowed('farming')
    row=next(a for a in c.overview()['agents'] if a['id']=='farming')
    assert row['working_seconds']>=0 and row['last_activity'] and row['status']=='DISABLED'


def test_paused_jobs_does_not_queue_periodic_checks(tmp_path):
    from monitor_runner import queue_due_monitoring
    store=Store(tmp_path/'db');AgentControls(store, registry=default_registry()).change('jobs',{'running':False})
    assert queue_due_monitoring(store, AgentControls(store, default_registry()))==[] and store.commands()==[]


def test_notification_read_and_presented_persist_without_resolving_action(dashboard):
    store=dashboard.store;action=store.add_action('Review fixture','submit_application')
    store.add_notification('Read me','fixture','CRITICAL',domain='jobs',related_page='actions')
    row=request(dashboard,'/api/notifications')[2][0]
    assert row['read']==0 and row['presented']==0
    assert request(dashboard,f"/api/notifications/{row['id']}",'POST',{'value':True})[0]==200
    assert request(dashboard,f"/api/notifications/{row['id']}",'POST',{'field':'presented','value':True})[0]==200
    row=Store(store.path).notifications()[0]
    assert row['read']==1 and row['presented']==1
    assert store.get_action(action)['status']=='PENDING'
    assert request(dashboard,'/api/notifications?read=0')[2]==[]


def test_notification_filters_do_not_delete_or_mark_read(dashboard):
    dashboard.store.add_notification('Soil report','north plot','INFO',domain='farming')
    dashboard.store.add_notification('Job report','application','CRITICAL',domain='jobs')
    rows=request(dashboard,'/api/notifications?agent=farming&q=soil&severity=INFO')[2]
    assert len(rows)==1 and rows[0]['read']==0
    assert len(dashboard.store.notifications())==2
    assert request(dashboard,'/api/notification-preferences','POST',{'delivery':'quiet','sort':'oldest'})[0]==200
    assert request(dashboard,'/api/notification-preferences')[2]['delivery']=='quiet'


def test_fact_edit_revokes_old_confirmation_and_preserves_history(dashboard):
    identity=dashboard.store.add_candidate_fact({'text':'Original','status':'USER_CONFIRMED'})
    assert request(dashboard,f'/api/facts/{identity}/edit','POST',{'text':'Corrected'})[0]==200
    row=dashboard.store.candidate_facts()[0]
    assert row['status']=='PROPOSED' and row['confirmed_at'] is None and row['text']=='Corrected'
    with dashboard.store._connect() as con:assert con.execute('SELECT text FROM fact_history WHERE fact_id=?',(identity,)).fetchone()[0]=='Original'


def test_schedule_add_pause_edit_remove_survives_seeding(dashboard):
    from monitoring import seed_monitoring,due_tasks
    result=request(dashboard,'/api/schedules','POST',{'task_type':'job_market_refresh','interval_days':3})
    assert result[0]==200;identity=result[2]['id']
    assert request(dashboard,f'/api/schedules/{identity}','POST',{'enabled':False,'interval_days':5})[0]==200
    assert identity not in [t['id'] for t in due_tasks(dashboard.store)]
    assert request(dashboard,f'/api/schedules/{identity}','POST',{'remove':True})[0]==200
    seed_monitoring(dashboard.store)
    row=next(t for t in dashboard.store.monitoring_tasks() if t['id']==identity)
    assert row['status']=='REMOVED' and row['enabled']==0


def test_email_save_masks_secret_and_preserves_environment(dashboard,monkeypatch):
    from control.settings import settings_path,environment
    monkeypatch.setenv('GROQ_API_KEY','unchanged-provider-fixture')
    data={'EMAIL_PROVIDER':'custom_smtp','SMTP_HOST':'smtp.example.org','SMTP_PORT':'587','SMTP_USERNAME':'test@example.org','SMTP_PASSWORD':'private-fixture','EMAIL_SENDER':'test@example.org','EMAIL_RECIPIENT':'test@example.org'}
    assert request(dashboard,'/api/settings/email','POST',data)[0]==200
    result=request(dashboard,'/api/settings/email')[2]
    assert result['password_configured'] and 'private-fixture' not in json.dumps(result)
    assert environment()['GROQ_API_KEY']=='unchanged-provider-fixture'
    assert request(dashboard,'/api/settings/email','POST',{'SMTP_PASSWORD':''})[0]==200
    assert environment()['SMTP_PASSWORD']=='private-fixture'
    assert settings_path().parent==dashboard.store.path.parent


def test_vm_login_disabled_at_backend(dashboard):
    request(dashboard,'/api/site-access','POST',{'url':'https://fixture.example/jobs'})
    code,_,body=request(dashboard,'/api/site-access/fixture.example','POST',{'action':'login'})
    assert code==403 and 'Folio' in body['reason']


@pytest.mark.parametrize('filename,data',[('x.exe',b'MZ'),('x.pdf',b'not a pdf'),('x.docx',b'not a zip'),('x.txt',b'\x00\xff')])
def test_reject_unsafe_uploads(dashboard,filename,data):
    assert request(dashboard,'/api/cvs','POST',{'filename':filename,'content_b64':base64.b64encode(data).decode()})[0]==400
    assert dashboard.store.cvs()==[]


def test_text_upload_download_roundtrip(dashboard):
    code,_,body=request(dashboard,'/api/cvs','POST',{'filename':'../../document.txt','content_b64':base64.b64encode(b'Plain fixture document').decode()})
    assert code==200
    code,headers,data=request(dashboard,f"/api/cvs/{body['id']}/download")
    assert code==200 and data==b'Plain fixture document' and headers['Content-Disposition'].startswith('attachment;')
    assert not (dashboard.app.ROOT/'document.txt').exists()


def test_report_preview_and_download_confined_to_registered_storage(dashboard):
    folder=dashboard.app.ROOT/'logs';folder.mkdir();file=folder/'audit.txt';file.write_text('Report <script> is plain text')
    dashboard.store.add_report('fixture',str(file));identity=dashboard.store.reports()[0]['id']
    assert request(dashboard,f'/api/reports/{identity}/preview')[2]['text']=='Report <script> is plain text'
    assert request(dashboard,f'/api/reports/{identity}/download')[2]==file.read_bytes()
    outside=dashboard.app.ROOT/'private.env';outside.write_text('SECRET')
    dashboard.store.add_report('invalid path',str(outside));identity=dashboard.store.reports()[0]['id']
    assert request(dashboard,f'/api/reports/{identity}/download')[0]==403


def test_upwork_browser_read_requires_approved_api():
    from browser.playwright_reader import PlaywrightReader,BrowserReadError
    with pytest.raises(BrowserReadError,match='approved API'):PlaywrightReader().read_url('https://www.upwork.com/jobs')


@pytest.mark.parametrize('outcome',['FAILED','BLOCKED','REVIEW','SUBMISSION_UNKNOWN','NOT_EXECUTED'])
def test_unsuccessful_approved_action_is_not_done(tmp_path,outcome):
    from types import SimpleNamespace
    from worker.command_processor import CommandProcessor
    store=Store(tmp_path/'db');identity=store.add_action('Fixture','fill_application_form')
    store.resolve_action(identity,'APPROVED')
    executor=SimpleNamespace(prepare=lambda *a,**kw:{'status':outcome,'reason':'Fixture result'})
    result=CommandProcessor(store,None,application_executor=executor).process_approved_action(identity)
    assert result['status']==outcome and store.get_action(identity)['status']==outcome
    assert store.notifications()[0]['related_page']=='applicationArchive'


def test_restart_requires_review_instead_of_replaying_interrupted_action(tmp_path):
    store=Store(tmp_path/'db');identity=store.add_action('Interrupted submission','submit_application')
    store.resolve_action(identity,'APPROVED');assert store.next_approved_action()['id']==identity
    store.reset_executing_actions()
    assert store.get_action(identity)['status']=='REVIEW'
    assert store.next_approved_action() is None
    assert len(store.notifications())==1
    store.reset_executing_actions();assert len(store.notifications())==1

def test_restart_does_not_count_downtime_as_work(tmp_path,monkeypatch):
    controls=AgentControls(Store(tmp_path/'db'), registry=default_registry())
    monkeypatch.setattr('control.agents.time.time',lambda:100)
    identity=controls.begin('jobs','fixture')
    monkeypatch.setattr('control.agents.time.time',lambda:110);controls.heartbeat()
    monkeypatch.setattr('control.agents.time.time',lambda:10000);controls.startup()
    row=next(a for a in controls.overview()['agents'] if a['id']=='jobs')
    assert row['working_seconds']==10 and row['recent_activity'][0]['outcome']=='INTERRUPTED'


def test_approved_action_activity_records_result(tmp_path,monkeypatch):
    from types import SimpleNamespace
    runtime=DomainRuntime(Store(tmp_path/'db'), registry=default_registry())
    _,agent=runtime.registry.resolve('jobs')
    monkeypatch.setattr(runtime,'_jobs',lambda:(SimpleNamespace(process_approved_action=lambda *a,**kw:{'status':'BLOCKED'}),agent))
    assert runtime.process_approved_action(123)['status']=='BLOCKED'
    row=next(a for a in runtime.controls.overview()['agents'] if a['id']=='jobs')
    assert row['recent_activity'][0]['outcome']=='BLOCKED'
    assert row['recent_activity'][0]['finished'] is not None


def test_farm_reminder_is_a_new_agent_notification(tmp_path):
    from domains.storage import initialize,deliver_due_reminders
    store=Store(tmp_path/'db');initialize(store)
    with store._connect() as con:con.execute("INSERT INTO domain_reminders(domain,title,due_at) VALUES('farming','Water fixture',0)")
    assert deliver_due_reminders(store, AgentControls(store, default_registry()))==1
    row=store.notifications()[0]
    assert row['domain']=='farming' and row['presented']==0
    assert deliver_due_reminders(store, AgentControls(store, default_registry()))==0


def test_real_summaries_use_history_and_deduplicate(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    from notifications.report import build_summary,deliver_summaries,summary_settings
    monkeypatch.chdir(tmp_path)
    store=Store(tmp_path/'db')
    cid=store.queue_command('Recorded completed work');store.update_command(cid,'COMPLETED','done')
    failed=store.queue_command('Recorded failed work');store.update_command(failed,'FAILED','Specific failure')
    store.add_action('Pending decision','review','Review fixture')
    with store._connect() as con:con.execute("UPDATE commands SET processed_at='2026-05-31 12:00:00'")
    now=datetime(2026,5,31,23,59,tzinfo=timezone.utc)
    summary=build_summary(store,'daily',now)
    assert len(summary.completed)==1 and 'Specific failure' in summary.blocked_items[0]
    assert summary.pending_actions[0].title=='Pending decision'
    assert len(deliver_summaries(store,now, registry=default_registry()))==3
    assert deliver_summaries(store,now, registry=default_registry())==[]
    assert len(store.reports())==3 and len(store.notifications())==3
    summary_settings(store,{'daily':False}, registry=default_registry())
    assert deliver_summaries(store,datetime(2026,6,1,23,59,tzinfo=timezone.utc), registry=default_registry())==[]


def test_valid_pdf_and_docx_and_active_content_rejection():
    from control.files import validate_document
    from pypdf import PdfWriter
    from docx import Document
    def encode(data):return base64.b64encode(data).decode()
    writer=PdfWriter();writer.add_blank_page(width=100,height=100);stream=io.BytesIO();writer.write(stream)
    assert validate_document('safe.pdf',encode(stream.getvalue()))[0]=='.pdf'
    writer.add_js('app.alert(1)');stream=io.BytesIO();writer.write(stream)
    with pytest.raises(ValueError):validate_document('active.pdf',encode(stream.getvalue()))
    stream=io.BytesIO();doc=Document();doc.add_paragraph('Fixture CV');doc.save(stream)
    assert validate_document('safe.docx',encode(stream.getvalue()))[0]=='.docx'
    unsafe=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(stream.getvalue())) as source,zipfile.ZipFile(unsafe,'w') as target:
        for item in source.infolist():target.writestr(item,source.read(item))
        target.writestr('word/_rels/evil.xml.rels','<Relationships><Relationship TargetMode = "External" Target="https://example.test" /></Relationships>')
    with pytest.raises(ValueError):validate_document('external.docx',encode(unsafe.getvalue()))


def test_oversized_upload_and_symlink_download_rejected(dashboard,tmp_path):
    from control.files import MAX_BYTES,validate_document
    with pytest.raises(ValueError):validate_document('large.txt',base64.b64encode(b'a'*(MAX_BYTES+1)).decode())
    folder=dashboard.app.ROOT/'logs';folder.mkdir();outside=tmp_path/'secret.txt';outside.write_text('secret')
    link=folder/'linked.txt'
    try:
        link.symlink_to(outside)
    except OSError as exc:
        # Creating a Windows symlink may require a privileged fixture-prep
        # step. The dashboard and rejection request still run unprivileged.
        fixture=os.getenv('CHIEF_TEST_SYMLINK_FIXTURE')
        if os.name!='nt' or getattr(exc,'winerror',None)!=1314 or not fixture:
            raise
        manifest_path=Path(fixture).resolve()
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        assert manifest['fixture']=='chief-symlink-denial-v1'
        root=manifest_path.parent/'dashboard-root'
        outside=manifest_path.parent/'outside-sentinel.txt'
        link=root/'logs/linked.txt'
        assert outside.read_text(encoding='utf-8')==manifest['sentinel']
        assert link.is_symlink() and link.resolve()==outside.resolve()
        assert not outside.resolve().is_relative_to(root.resolve())
        dashboard.app.ROOT=root
    dashboard.store.add_report('linked',str(link));identity=dashboard.store.reports()[0]['id']
    assert request(dashboard,f'/api/reports/{identity}/download')[0]==403
