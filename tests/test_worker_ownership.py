import os
from pathlib import Path
import subprocess
import sys
import pytest
from database.store import Store
from worker.ownership import WorkerOwnership,WorkerAlreadyRunning


def test_second_worker_cannot_open_store_or_recover_live_actions(tmp_path,monkeypatch):
    from worker import runner
    from deployment.instance import Instance
    path=tmp_path/'worker.db';store=Store(path)
    action=store.add_action('Synthetic live action','fixture');store.resolve_action(action,'APPROVED')
    assert store.next_approved_action()['id']==action
    monkeypatch.setenv('JOB_WORKER_DB',str(path))
    with WorkerOwnership(path):
        monkeypatch.setattr(Instance,'open_store',lambda *a:pytest.fail('A second worker must not open/mutate the instance.'))
        with pytest.raises(WorkerAlreadyRunning):runner.main()
    assert store.get_action(action)['status']=='EXECUTING'


def test_os_lock_releases_after_actual_process_death(tmp_path):
    path=tmp_path/'worker.db'
    # Crash the actual interpreter, not only Windows' venv launcher parent.
    script='from worker.ownership import WorkerOwnership; import sys,os; owner=WorkerOwnership(sys.argv[1]); owner.__enter__(); print("READY",flush=True); sys.stdin.readline(); os._exit(17)'
    child=subprocess.Popen([sys.executable,'-c',script,str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='READY'
        with pytest.raises(WorkerAlreadyRunning):
            with WorkerOwnership(path):pass
        child.stdin.write('crash\n');child.stdin.flush();child.wait(timeout=10)
        assert child.returncode==17
        with WorkerOwnership(path):assert path.with_name('worker.db.worker.lock').exists()
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=10)
        child.stdin.close();child.stdout.close();child.stderr.close()
    # Lock file is retained; deleting a locked inode would permit split ownership.
    with WorkerOwnership(path):pass


def test_releasing_owner_never_deletes_lock_file(tmp_path):
    path=tmp_path/'worker.db'
    with WorkerOwnership(path) as owner:identity=owner.path.stat().st_ino
    with WorkerOwnership(path) as owner:assert owner.path.stat().st_ino==identity


def test_two_real_worker_startups_preserve_live_work_and_recover_once(tmp_path,monkeypatch):
    from tests.checkpoint_f_fixture import secure_store
    path=tmp_path/'worker.db';store=Store(path);secure_store(store)
    monkeypatch.setenv('JOB_WORKER_DB',str(path))
    script='''from worker import runner
from worker.ownership import WorkerAlreadyRunning
import sys
runner.build_gateway=lambda **kwargs: object()
def hold(processor):
    print('READY',flush=True)
    sys.stdin.readline()
    raise KeyboardInterrupt
runner.run_approved_once=hold
try: sys.exit(runner.main())
except WorkerAlreadyRunning: print('REFUSED',flush=True)
'''
    first=subprocess.Popen([sys.executable,'-c',script],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert first.stdout.readline().strip()=='READY'
        action=store.add_action('Synthetic in-flight action','fixture');store.resolve_action(action,'APPROVED')
        assert store.next_approved_action()['id']==action
        second=subprocess.run([sys.executable,'-c',script],input='\n',capture_output=True,text=True,timeout=15)
        assert second.returncode==0 and second.stdout.strip()=='REFUSED'
        assert store.get_action(action)['status']=='EXECUTING'
        first.stdin.write('\n');first.stdin.flush();first.wait(timeout=15)
        assert first.returncode==0
        recovered=subprocess.run([sys.executable,'-c',script],input='\n',capture_output=True,text=True,timeout=15)
        assert recovered.returncode==0 and recovered.stdout.strip()=='READY'
        assert store.get_action(action)['status']=='REVIEW' and store.next_approved_action() is None
        again=subprocess.run([sys.executable,'-c',script],input='\n',capture_output=True,text=True,timeout=15)
        assert again.returncode==0 and store.get_action(action)['status']=='REVIEW'
    finally:
        if first.poll() is None:first.kill();first.wait(timeout=10)
        first.stdin.close();first.stdout.close();first.stderr.close()


def test_lock_refuses_symlink(tmp_path):
    from operations.backup import BackupError
    owner=WorkerOwnership(tmp_path/'worker.db')
    if os.name=='nt':
        import json
        manifest_path=Path(os.environ['CHIEF_TEST_SYMLINK_FIXTURE'])
        fixture=json.loads(manifest_path.read_text())
        assert fixture['fixture']=='chief-symlink-denial-v1'
        target=manifest_path.parent/'outside-sentinel.txt'
        owner.path=manifest_path.parent/'dashboard-root/logs/linked.txt'
        assert owner.path.is_symlink() and owner.path.resolve()==target.resolve()
        before=target.read_text();assert before==fixture['sentinel']
    else:
        target=tmp_path/'outside';target.write_text('must remain unchanged');before=target.read_text()
        owner.path.symlink_to(target)
    with pytest.raises(BackupError):
        with owner:pass
    assert target.read_text()==before
