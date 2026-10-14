from types import SimpleNamespace

import pytest

from deployment.service_runtime import atomic_json, control_paths
from deployment import linux_service_control as services


def _record(instance, component, run_id, state='READY'):
    status, _ = control_paths(instance, component)
    atomic_json(status, {'version':1, 'component':component, 'run_id':run_id,
                         'pid':1, 'started_at':'2026-10-14T00:00:00Z',
                         'updated_at':'2026-10-14T00:00:00Z',
                         'heartbeat_at':'2026-10-14T00:00:00Z', 'state':state})


def test_stop_uses_only_fixed_managed_units(monkeypatch):
    calls = []
    monkeypatch.setattr(services, '_systemctl', lambda *args, timeout=60: calls.append(args))
    result = services.stop_services()
    assert result['status'] == 'SERVICES_STOPPED'
    assert calls == [('stop', 'chief-agent-scheduler.service', 'chief-agent-worker.service',
                      'chief-agent-dashboard.service')]


def test_start_requires_fresh_ready_records(monkeypatch, tmp_path):
    instance = SimpleNamespace(state_root=tmp_path)
    for component in ('dashboard', 'worker', 'scheduler'):
        _record(instance, component, component[0] * 32)

    calls = []
    def fake_systemctl(*args, timeout=60):
        calls.append(args)
        if args[0] == 'start':
            _record(instance, 'dashboard', 'd' * 31 + '1')
            _record(instance, 'worker', 'w' * 31 + '1')
            _record(instance, 'scheduler', 's' * 31 + '1')
    monkeypatch.setattr(services, '_systemctl', fake_systemctl)
    result = services.start_services(instance, timeout=1)
    assert result['status'] == 'SERVICES_READY'
    assert calls == [('start', 'chief-agent-dashboard.service', 'chief-agent-worker.service',
                      'chief-agent-scheduler.service')]


def test_start_rejects_stale_ready_records(monkeypatch, tmp_path):
    instance = SimpleNamespace(state_root=tmp_path)
    for component in ('dashboard', 'worker', 'scheduler'):
        _record(instance, component, component[0] * 32)
    monkeypatch.setattr(services, '_systemctl', lambda *args, timeout=60: None)
    with pytest.raises(RuntimeError, match='fresh READY'):
        services.start_services(instance, timeout=.05)
