import json
import subprocess
import sys
import pytest
from database.store import Store
from application.composition import default_registry
from scheduler import AutonomousScheduler,SchedulerReviewRequired
from worker.ownership import ComponentOwnership,WorkerAlreadyRunning


def test_interrupted_effect_never_replayed_and_worker_state_unchanged(tmp_path,monkeypatch):
    store=Store(tmp_path/'worker.db');store.set_worker('IDLE','worker remains independent')
    scheduler=AutonomousScheduler(store,registry=default_registry());effects=[]
    def effect():effects.append('sent');raise RuntimeError('private failure')
    monkeypatch.setattr(scheduler,'_run_due_once',effect)
    with pytest.raises(RuntimeError):scheduler.run_due_once()
    with pytest.raises(SchedulerReviewRequired):scheduler.run_due_once()
    assert effects==['sent']
    with store._connect() as con:
        record=json.loads(con.execute("SELECT value FROM control_state WHERE key='scheduler_cycle_v1'").fetchone()[0])
        assert record['state']=='IN_PROGRESS' and record['started_at'].endswith('Z')
        assert con.execute('SELECT message FROM worker_status').fetchone()[0]=='worker remains independent'


def test_actual_process_death_retains_ambiguous_cycle(tmp_path,monkeypatch):
    store=Store(tmp_path/'worker.db')
    script='''from scheduler import AutonomousScheduler
from database.store import Store
from application.composition import default_registry
import sys,os
s=AutonomousScheduler(Store(sys.argv[1]),registry=default_registry())
def effect():
 print('EFFECT',flush=True)
 sys.stdin.readline()
 os._exit(17)
s._run_due_once=effect
s.run_due_once()
'''
    child=subprocess.Popen([sys.executable,'-c',script,str(store.path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='EFFECT'
        child.stdin.write('crash\n');child.stdin.flush();child.wait(timeout=10)
        assert child.returncode==17
        scheduler=AutonomousScheduler(store,registry=default_registry())
        monkeypatch.setattr(scheduler,'_run_due_once',lambda:pytest.fail('Cannot replay'))
        with pytest.raises(SchedulerReviewRequired):scheduler.run_due_once()
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=10)
        child.stdin.close();child.stdout.close();child.stderr.close()


def test_cycle_lock_denies_second_caller_before_effect(tmp_path,monkeypatch):
    store=Store(tmp_path/'worker.db');scheduler=AutonomousScheduler(store,registry=default_registry())
    monkeypatch.setattr(scheduler,'_run_due_once',lambda:pytest.fail('No second sink'))
    with ComponentOwnership(store.path,'scheduler-cycle'):
        with pytest.raises(WorkerAlreadyRunning):scheduler.run_due_once()


def test_success_allows_next_cycle_and_preserves_legacy_rows(tmp_path,monkeypatch):
    store=Store(tmp_path/'worker.db');action=store.add_action('Keep exact','fixture');scheduler=AutonomousScheduler(store,registry=default_registry())
    before=store.get_action(action);effects=[]
    monkeypatch.setattr(scheduler,'_run_due_once',lambda:effects.append(1) or {'ok':True})
    assert scheduler.run_due_once()=={'ok':True};scheduler.run_due_once()
    assert effects==[1,1] and store.get_action(action)==before


def test_audit_default_output_uses_private_instance(tmp_path,monkeypatch):
    from notifications.daily_full_audit import send_daily_full_audit
    store=Store(tmp_path/'worker.db')
    result=send_daily_full_audit(store)
    from pathlib import Path
    assert Path(result['txt_path']).is_relative_to(tmp_path)


def test_recovery_record_survives_verified_backup_without_schema_change(tmp_path,monkeypatch):
    import sqlite3
    from tests.checkpoint_f_fixture import secure_store,recovery_point
    from deployment.schema import fingerprint
    store=Store(tmp_path/'worker.db');secure_store(store)
    with store._connect() as con:before=fingerprint(con)
    scheduler=AutonomousScheduler(store,registry=default_registry())
    monkeypatch.setattr(scheduler,'_run_due_once',lambda:(_ for _ in ()).throw(RuntimeError('Interrupted')))
    with pytest.raises(RuntimeError):scheduler.run_due_once()
    with store._connect() as con:
        assert fingerprint(con)==before
        expected=con.execute("SELECT value FROM control_state WHERE key='scheduler_cycle_v1'").fetchone()[0]
    drill=recovery_point(store,tmp_path)
    with sqlite3.connect(drill['restore_directory']/'state.sqlite3') as con:
        assert con.execute("SELECT value FROM control_state WHERE key='scheduler_cycle_v1'").fetchone()[0]==expected
    from operations.restore_guard import RestoreQuarantined
    with pytest.raises(RestoreQuarantined):Store(drill['restore_directory']/'state.sqlite3')
