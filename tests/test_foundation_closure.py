"""Independent-review reproductions through real Job composition, synthetic data."""
import json
import os
from pathlib import Path
import sqlite3
import pytest

from database.store import Store
from domains.jobs import make_processor
from tests.test_application_generation import SequenceGateway, valid_payload
from operations.draft_files import draft_files
from application.composition import default_registry
from control.agents import AgentControls
from tests.checkpoint_f_fixture import e_base, f_fixture


def prepared(tmp_path, monkeypatch):
    root = tmp_path / 'state'
    root.mkdir()
    monkeypatch.setenv('CHIEF_STATE_ROOT', str(root))
    store = Store(root / 'worker.db')
    store.add_candidate_fact({'text': 'Nmap', 'status': 'USER_CONFIRMED'})
    store.add_job({'id': 'j1', 'title': 'SOC Analyst', 'url': 'https://fixture.test/job'})
    return store, root


def execute(store, application_id='safe-draft', job=None):
    payload = {'application_id': application_id, 'job': job or {'id': 'j1', 'title': 'Untrusted title'}}
    plan = {'actions': [{'action': 'save_application_draft', 'payload': payload}]}
    gateway = SequenceGateway([json.dumps(plan), json.dumps(valid_payload())])
    cid = store.queue_command('Create a truthful draft.')
    return make_processor(store, gateway).process_command(cid, 'Create a truthful draft.')


@pytest.mark.parametrize('identity', ['../../../../escaped', '../escape', '/absolute', r'C:\outside', r'\\server\share',
                                     r'..\outside', 'x:y', 'x.', 'x ', '.', '..', '', 'CON', 'nul', 'COM1', 'a/b',
                                     'a\x00b', '\u2215outside', 17, None, 'x' * 81])
def test_composed_job_rejects_unsafe_id_without_files(tmp_path, monkeypatch, identity):
    store, root = prepared(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        execute(store, identity)
    assert not (root / 'candidate').exists()
    assert not store.applications()
    assert not list(tmp_path.rglob('*cv.json'))


def test_missing_job_rejected_before_file_effect(tmp_path, monkeypatch):
    store, root = prepared(tmp_path, monkeypatch)
    execute(store, '../../../../escaped', {'id': 'absent', 'title': 'SOC Analyst'})
    assert not (root / 'candidate').exists()
    assert not store.applications()


def test_composed_success_uses_stored_job_and_preserves_duplicate_history(tmp_path, monkeypatch):
    store, root = prepared(tmp_path, monkeypatch)
    result = execute(store)
    assert not result['approvals']
    app = store.applications()[0]
    assert app['id'] == 'safe-draft'
    path = Path(app['cv_path'])
    assert path.is_relative_to(root / 'candidate' / 'application_history' / 'drafts')
    before = path.read_bytes(), store.application_snapshots(app['id']), store.application_events(app['id'])
    with pytest.raises(ValueError):
        execute(store)
    assert (path.read_bytes(), store.application_snapshots(app['id']), store.application_events(app['id'])) == before


@pytest.mark.parametrize('table', ['applications', 'application_snapshots', 'application_events'])
def test_database_failure_rolls_back_files_and_all_history(tmp_path, monkeypatch, table):
    store, root = prepared(tmp_path, monkeypatch)
    with store._connect() as con:
        con.execute(f"CREATE TRIGGER fail_draft BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        execute(store)
    assert not (root / 'candidate' / 'application_history' / 'drafts' / 'safe-draft').exists()
    with store._connect() as con:
        for name in ('applications', 'application_snapshots', 'application_events'):
            assert con.execute('SELECT count(*) FROM ' + name).fetchone()[0] == 0


def test_existing_destination_is_never_overwritten(tmp_path, monkeypatch):
    store, root = prepared(tmp_path, monkeypatch)
    folder = root / 'candidate' / 'application_history' / 'drafts' / 'safe-draft'
    folder.mkdir(parents=True)
    sentinel = folder / 'cv.json'
    sentinel.write_text('preserve')
    with pytest.raises(FileExistsError):
        execute(store)
    assert sentinel.read_text() == 'preserve'
    assert not store.applications()


def test_two_composed_drafts_cannot_replace_each_other(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    store, root = prepared(tmp_path, monkeypatch)
    def attempt():
        try:
            execute(store)
            return 'CREATED'
        except (ValueError, FileExistsError):
            return 'REJECTED'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == ['CREATED', 'REJECTED']
    assert len(store.applications()) == 1
    assert len(store.application_snapshots('safe-draft')) == 1
    assert len(store.application_events('safe-draft')) == 1


def test_process_death_leaves_only_contained_inert_files(tmp_path, monkeypatch):
    import subprocess
    import sys
    store, root = prepared(tmp_path, monkeypatch)
    script = '''import os,sys
from operations.draft_files import draft_files
with draft_files(sys.argv[1], 'interrupted', {'cv':{},'cover_letter':'synthetic'}):
    os._exit(19)
'''
    run = subprocess.run([sys.executable, '-c', script, str(root)], capture_output=True, timeout=20)
    assert run.returncode == 19
    folder = root / 'candidate' / 'application_history' / 'drafts' / 'interrupted'
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    assert set(before) == {'cv.json', 'cover-letter.txt'}
    with pytest.raises(FileExistsError):
        execute(store, 'interrupted')
    assert not store.applications()
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_commit_failure_cleans_files_and_rolls_back(tmp_path, monkeypatch):
    store, root = prepared(tmp_path, monkeypatch)
    original = store._connect
    class FailingCommit:
        def __init__(self): self.connection = original()
        def __getattr__(self, name): return getattr(self.connection, name)
        def commit(self): raise sqlite3.OperationalError('synthetic commit error')
    from domains.jobs.draft_storage import persist_draft
    monkeypatch.setattr(store, '_connect', FailingCommit)
    with pytest.raises(sqlite3.OperationalError):
        persist_draft(store, root, 'failed-commit', {'id':'j1'}, valid_payload())
    monkeypatch.setattr(store, '_connect', original)
    assert not store.applications()
    assert not (root / 'candidate' / 'application_history' / 'drafts' / 'failed-commit').exists()


def test_file_failure_removes_only_new_operation(tmp_path, monkeypatch):
    from operations import draft_files as module
    store, root = prepared(tmp_path, monkeypatch)
    real = module.os.fsync
    def fail(fd):
        import stat
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError('synthetic disk error')
        return real(fd)
    monkeypatch.setattr(module.os, 'fsync', fail)
    with pytest.raises(OSError):
        with draft_files(root, 'safe-draft', valid_payload()):
            pytest.fail('Cannot publish incomplete files')
    assert not (root / 'candidate' / 'application_history' / 'drafts' / 'safe-draft').exists()


@pytest.mark.parametrize('ancestor', ['candidate', 'candidate/application_history', 'candidate/application_history/drafts'])
def test_directory_link_cannot_redirect_writer(tmp_path, monkeypatch, ancestor):
    store, root = prepared(tmp_path, monkeypatch)
    outside = tmp_path / 'outside'
    outside.mkdir()
    link = root / ancestor
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        # Directory junction creation needs no symbolic-link privilege.
        import subprocess
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
        assert result.returncode == 0
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises((ValueError, OSError)):
            execute(store)
        assert list(outside.iterdir()) == []
        assert not store.applications()
    finally:
        if os.name == 'nt':
            os.rmdir(link)
        else:
            link.unlink()


def test_approval_service_rederives_domain(f_fixture):
    f = f_fixture
    action = f.store.add_action('Synthetic', 'fill_application_form')
    f.identity.approve_action(f.owner, action, 'jobs')
    assert f.identity.approval_valid(action, 'jobs')
    assert not f.identity.approval_valid(action, 'farming')


def test_monitor_public_helper_shares_cycle_lock(tmp_path):
    from monitor_runner import queue_due_monitoring
    from worker.ownership import ComponentOwnership, WorkerAlreadyRunning
    store = Store(tmp_path / 'worker.db')
    controls = AgentControls(store, default_registry())
    with ComponentOwnership(store.path, 'scheduler-cycle'):
        with pytest.raises(WorkerAlreadyRunning):
            queue_due_monitoring(store, controls)
    assert not store.commands()
    assert len(queue_due_monitoring(store, controls)) == 2
    assert queue_due_monitoring(store, controls) == []


def test_monitor_interrupted_cycle_blocks_both_entry_points(tmp_path, monkeypatch):
    import monitor_runner
    from scheduler import AutonomousScheduler, SchedulerReviewRequired
    store = Store(tmp_path / 'worker.db')
    controls = AgentControls(store, default_registry())
    calls = []
    def interrupt(*args):
        calls.append(1)
        raise RuntimeError('synthetic interruption')
    monkeypatch.setattr(monitor_runner, '_queue_due_monitoring', interrupt)
    with pytest.raises(RuntimeError):
        monitor_runner.queue_due_monitoring(store, controls)
    with pytest.raises(SchedulerReviewRequired):
        monitor_runner.queue_due_monitoring(store, controls)
    with pytest.raises(SchedulerReviewRequired):
        AutonomousScheduler(store, registry=default_registry()).run_due_once()
    assert calls == [1]
