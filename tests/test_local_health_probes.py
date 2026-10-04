import json
import sqlite3
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from capabilities.contracts import Node
from control.health import SystemHealth
from control.health_contracts import HealthStatus as H
from control.local_probes import database, service
from deployment.instance import Instance
from deployment.service_runtime import ServiceRuntime, read_record
from operations.time_integrity import utc_now, utc_text
from checkpoint_c_fixture import migrated, unmigrated


def test_database_read_health_does_not_create_or_modify_database(tmp_path):
    store = SimpleNamespace(path=tmp_path/'missing.db')
    assert database(store, utc_now).status == H.UNAVAILABLE
    assert not store.path.exists()
    with sqlite3.connect(store.path) as con:con.execute('CREATE TABLE evidence(value TEXT)')
    before = store.path.read_bytes()
    observed = database(store, utc_now)
    assert observed.status == H.HEALTHY
    assert store.path.read_bytes() == before
    store.path.write_bytes(b'not a sqlite database')
    assert database(store, utc_now).status == H.UNAVAILABLE


def test_missing_service_record_does_not_create_control_directory(tmp_path, monkeypatch):
    monkeypatch.setenv('CHIEF_STATE_ROOT', str(tmp_path))
    assert service(SimpleNamespace(path=tmp_path/'db'), 'dashboard', utc_now).status == H.UNKNOWN
    assert not (tmp_path/'service-control').exists()


@pytest.mark.parametrize('change', [
    {'heartbeat_at':None}, {'heartbeat_at':'bad'},
    {'heartbeat_at':utc_text(utc_now()-timedelta(minutes=5))},
    {'heartbeat_at':utc_text(utc_now()+timedelta(minutes=5))},
    {'component':'worker'}, {'version':9}, {'state':'something-else'},
])
def test_old_stale_malformed_or_wrong_service_record_is_not_healthy(migrated, monkeypatch, change):
    root=migrated.store.path.parent; monkeypatch.setenv('CHIEF_STATE_ROOT',str(root))
    folder=root/'service-control';folder.mkdir()
    data={'version':1,'component':'dashboard','state':'READY','heartbeat_at':utc_text(utc_now()),**change}
    (folder/'dashboard.json').write_text(json.dumps(data))
    result={r.node.id:r for r in SystemHealth(migrated.services.controls).snapshot()}
    assert result['chief.dashboard'].status == H.UNKNOWN


def test_live_service_pulses_without_reverting_state_and_reports_stop(tmp_path, monkeypatch):
    monkeypatch.setenv('CHIEF_STATE_ROOT', str(tmp_path))
    instance=Instance('test',tmp_path,tmp_path/'db')
    store=SimpleNamespace(path=instance.database)
    with ServiceRuntime(instance,'dashboard') as runtime:
        runtime.ready(); first=read_record(runtime.status)['heartbeat_at']
        deadline=time.monotonic()+8
        while read_record(runtime.status)['heartbeat_at']==first and time.monotonic()<deadline:
            time.sleep(.1)
        record=read_record(runtime.status)
        assert record['state']=='READY' and record['heartbeat_at']!=first
        observation=service(store,'dashboard',utc_now)
        assert observation.status==H.HEALTHY
        assert 'task progress is not established' in observation.reason
        runtime.stopping.set(); runtime._write()
        assert service(store,'dashboard',utc_now).status==H.DEGRADED
    assert service(store,'dashboard',utc_now).status==H.UNAVAILABLE


def test_failed_pulse_stops_service_without_leaking_error(tmp_path, monkeypatch):
    instance=Instance('test',tmp_path,tmp_path/'db')
    with ServiceRuntime(instance,'scheduler') as runtime:
        runtime.ready()
        original=runtime._write
        def fail(*args):raise OSError('synthetic private detail')
        monkeypatch.setattr(runtime,'_write',fail)
        assert runtime.stopping.wait(8)
        monkeypatch.setattr(runtime,'_write',original)


def test_database_observation_does_not_qualify_provider_browser_or_tasks(migrated):
    result={r.node.id:r for r in SystemHealth(migrated.services.controls).snapshot()}
    assert result['chief.database'].status==H.HEALTHY
    assert result['chief.browser'].status==H.UNKNOWN
    assert result['chief.gateway'].status==H.UNKNOWN
    assert result['jobs.execute'].status==H.UNKNOWN
