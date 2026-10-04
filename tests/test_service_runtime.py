import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
import pytest
from database.store import Store
from deployment.instance import Instance
from deployment.service_runtime import ServiceRuntime,request_stop,read_record
from tests.checkpoint_f_fixture import secure_store,recovery_point


def prepared(root):
    from control.agents import AgentControls
    from application.composition import default_registry
    from notifications.report import summary_settings
    from datetime import datetime
    from zoneinfo import ZoneInfo
    store=Store(root/'worker.db');secure_store(store)
    registry=default_registry();controls=AgentControls(store,registry)
    for domain in registry.domains:controls.change(domain,{'enabled':False})
    summary_settings(store,{'daily':False,'weekly':False,'monthly':False},registry=registry)
    store.add_report('full_audit:'+datetime.now(ZoneInfo('Africa/Lagos')).date().isoformat(),'synthetic-no-send')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    config=root/'service.json'
    config.write_text(json.dumps({'mode':'ISOLATED_DEVELOPMENT','state_root':str(root),'database':str(store.path),'dashboard_port':port}))
    return store,config


def command(config,component):
    return [sys.executable,'-m','deployment.launch','--component',component,'--config',str(config)]


def wait_state(root,component,state,child=None):
    path=root/'service-control'/(component+'.json');deadline=time.monotonic()+25
    while time.monotonic()<deadline:
        if child is not None and child.poll() is not None:raise AssertionError('Service exited before readiness: '+child.communicate()[0])
        try:
            record=read_record(path)
            if record['state']==state:return record
        except FileNotFoundError:pass
        time.sleep(.05)
    raise AssertionError('Service readiness timed out')


@pytest.mark.parametrize('component',['dashboard','worker','scheduler'])
def test_actual_service_start_duplicate_stop_restart(tmp_path,monkeypatch,component):
    store,config=prepared(tmp_path)
    # Invalid synthetic tokens only; all domain work is disabled and no provider calls occur.
    monkeypatch.setenv('GROQ_API_KEY','SYNTHETIC_INVALID_FIXTURE')
    monkeypatch.setenv('MISTRAL_API_KEY','SYNTHETIC_INVALID_FIXTURE')
    monkeypatch.setenv('WORKER_POLL_SECONDS','.1');monkeypatch.setenv('SCHEDULER_POLL_SECONDS','.1')
    for attempt in range(2):
        child=subprocess.Popen(command(config,component),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        try:
            record=wait_state(tmp_path,component,'READY',child)
            if attempt==1:assert record['run_id']!=previous
            duplicate=subprocess.run(command(config,component),capture_output=True,text=True,timeout=20)
            assert duplicate.returncode==1 and 'Traceback' not in duplicate.stdout+duplicate.stderr
            assert read_record(tmp_path/'service-control'/(component+'.json'))['run_id']==record['run_id']
            stop=subprocess.run(command(config,component)+['--stop'],capture_output=True,text=True,timeout=20)
            assert stop.returncode==0
            output=child.communicate(timeout=20)[0]
            assert child.returncode==0 and 'Traceback' not in output
            assert read_record(tmp_path/'service-control'/(component+'.json'))['state']=='STOPPED'
            previous=record['run_id']
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=10)
            child.stdout.close()
    assert store.counts()['commands']==0


def test_stale_stop_does_not_stop_replacement(tmp_path):
    instance=Instance('test',tmp_path,tmp_path/'worker.db')
    with ServiceRuntime(instance,'worker') as first:
        first.ready();assert request_stop(instance,'worker');assert first.stopping.wait(2)
    with ServiceRuntime(instance,'worker') as second:
        second.ready();assert not second.stopping.wait(.4)


def test_status_updates_with_concurrent_reader(tmp_path):
    from deployment.service_runtime import atomic_json
    from threading import Thread,Event
    path=tmp_path/'status.json';atomic_json(path,{'number':0});done=Event();failures=[]
    def reader():
        while not done.is_set():
            try:assert isinstance(read_record(path)['number'],int)
            except Exception as exc:failures.append(type(exc).__name__)
    thread=Thread(target=reader);thread.start()
    try:
        for number in range(1,100):atomic_json(path,{'number':number})
    finally:done.set();thread.join(timeout=5)
    assert not thread.is_alive() and not failures and read_record(path)=={'number':99}


def test_status_failed_write_preserves_previous_record(tmp_path,monkeypatch):
    from deployment.service_runtime import atomic_json
    path=tmp_path/'status.json';atomic_json(path,{'state':'READY'})
    def failure(*a):raise OSError('synthetic disk failure')
    monkeypatch.setattr(os,'replace',failure)
    with pytest.raises(OSError):atomic_json(path,{'state':'STOPPED'})
    assert read_record(path)=={'state':'READY'} and list(tmp_path.glob('*.tmp'))==[]


@pytest.mark.parametrize('component',['dashboard','worker','scheduler'])
def test_missing_identity_refuses_real_service_before_commands(tmp_path,component):
    store=Store(tmp_path/'worker.db');before=store.queue_command('Must remain queued')
    config=tmp_path/'service.json';config.write_text(json.dumps({'mode':'ISOLATED_DEVELOPMENT','state_root':str(tmp_path),'database':str(store.path)}))
    result=subprocess.run(command(config,component),capture_output=True,text=True,timeout=20)
    assert result.returncode==1 and 'Traceback' not in result.stdout+result.stderr
    with store._connect() as con:assert con.execute('SELECT status FROM commands WHERE id=?',(before,)).fetchone()[0]=='QUEUED'


@pytest.mark.parametrize('field,value',[('api_key','SYNTHETIC_SECRET_NEVER_LOG'),('dashboard_port',True),('mode','preview')])
def test_invalid_config_no_secret_log_or_state_change(tmp_path,field,value):
    store,config=prepared(tmp_path);data=json.loads(config.read_text());data[field]=value;config.write_text(json.dumps(data))
    result=subprocess.run(command(config,'worker'),capture_output=True,text=True,timeout=15)
    assert result.returncode==1 and 'SYNTHETIC_SECRET' not in result.stdout+result.stderr
    assert not (tmp_path/'service-control').exists()


@pytest.mark.parametrize('component',['dashboard','worker','scheduler'])
def test_verified_restore_remains_quarantined_for_all_services(tmp_path,monkeypatch,component):
    store,config=prepared(tmp_path);drill=recovery_point(store,tmp_path)
    restored=list(drill['restore_directory'].rglob('*.sqlite3'))
    if not restored:restored=list(drill['restore_directory'].rglob('*.db'))
    assert len(restored)==1
    data=json.loads(config.read_text());data['database']=str(restored[0]);config.write_text(json.dumps(data))
    monkeypatch.setenv('JOB_WORKER_DB',str(restored[0]))
    result=subprocess.run(command(config,component),capture_output=True,text=True,timeout=20)
    assert result.returncode==1
    assert not (tmp_path/'service-control').exists() or not (tmp_path/'service-control'/(component+'.json')).exists() or read_record(tmp_path/'service-control'/(component+'.json'))['state']!='READY'
    from operations.restore_guard import assert_not_quarantined,RestoreQuarantined
    with pytest.raises(RestoreQuarantined):assert_not_quarantined(restored[0])


def test_worker_cooperative_stop_finishes_current_operation_only(tmp_path,monkeypatch):
    from worker import runner
    from threading import Event
    store,config=prepared(tmp_path);stop=Event();calls=[]
    monkeypatch.setattr(runner,'build_gateway',lambda **kw:object())
    def operation(processor):calls.append('current');stop.set();return False
    monkeypatch.setattr(runner,'run_approved_once',operation)
    monkeypatch.setattr(runner,'run_once',lambda *a:pytest.fail('Must not claim next work'))
    assert runner._owned_main(store,stop)==0 and calls==['current']


def test_real_worker_retains_ownership_while_draining(tmp_path):
    store,config=prepared(tmp_path)
    script='''from deployment.launch import configure
from worker import runner
from pathlib import Path
import sys,time
i=configure(sys.argv[1]);runner.build_gateway=lambda **kw:object()
def hold(processor):
 (i.state_root/'in-operation').write_text('synthetic')
 while not (i.state_root/'release-operation').exists():time.sleep(.05)
 return False
runner.run_approved_once=hold
def forbidden(processor):
 (i.state_root/'unexpected-next-work').write_text('FAIL')
 return False
runner.run_once=forbidden
sys.exit(runner.main())
'''
    child=subprocess.Popen([sys.executable,'-c',script,str(config)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    try:
        wait_state(tmp_path,'worker','READY',child)
        deadline=time.monotonic()+10
        while not (tmp_path/'in-operation').exists() and time.monotonic()<deadline:time.sleep(.05)
        assert (tmp_path/'in-operation').exists()
        instance=Instance('test',tmp_path,store.path);assert request_stop(instance,'worker')
        time.sleep(.4);assert child.poll() is None
        duplicate=subprocess.run(command(config,'worker'),capture_output=True,text=True,timeout=15)
        assert duplicate.returncode==1
        (tmp_path/'release-operation').write_text('release')
        output=child.communicate(timeout=15)[0]
        assert child.returncode==0 and 'Traceback' not in output and not (tmp_path/'unexpected-next-work').exists()
    finally:
        (tmp_path/'release-operation').touch()
        if child.poll() is None:child.wait(timeout=15)
        child.stdout.close()


def test_configuration_cannot_select_outside_private_root(tmp_path):
    store,config=prepared(tmp_path)
    data=json.loads(config.read_text());data['state_root']=str(tmp_path/'nested')
    (tmp_path/'nested').mkdir();config.write_text(json.dumps(data))
    result=subprocess.run(command(config,'worker'),capture_output=True,text=True,timeout=15)
    assert result.returncode==1 and not (tmp_path/'service-control').exists()


@pytest.mark.skipif(os.name=='nt',reason='POSIX SIGTERM; Windows cooperative-stop entry point tested natively')
def test_real_scheduler_sigterm_clean_stop(tmp_path):
    store,config=prepared(tmp_path)
    child=subprocess.Popen(command(config,'scheduler'),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    try:
        wait_state(tmp_path,'scheduler','READY',child);child.terminate();output=child.communicate(timeout=15)[0]
        assert child.returncode==0 and 'Traceback' not in output
        assert read_record(tmp_path/'service-control/scheduler.json')['state']=='STOPPED'
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=10)
        child.stdout.close()
