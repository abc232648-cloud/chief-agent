import pytest

from application.farm_release import require_farm_runtime


def clear(monkeypatch):
    for key in ('CHIEF_INSTANCE_MODE','CHIEF_SERVICE_CONFIGURED','CHIEF_FARM_PRODUCTION',
                'CHIEF_TRUSTED_PROXY','DASHBOARD_HOST'):
        monkeypatch.delenv(key,raising=False)


def test_test_and_preview_farm_runtime_remain_available(monkeypatch):
    clear(monkeypatch)
    for mode in ('test','preview'):
        monkeypatch.setenv('CHIEF_INSTANCE_MODE',mode)
        require_farm_runtime()


def test_production_never_opens_from_mode_or_raw_opt_in_alone(monkeypatch):
    clear(monkeypatch);monkeypatch.setenv('CHIEF_INSTANCE_MODE','production')
    with pytest.raises(PermissionError,match='validated service configuration'):
        require_farm_runtime()
    monkeypatch.setenv('CHIEF_FARM_PRODUCTION','ENABLED')
    with pytest.raises(PermissionError,match='validated service configuration'):
        require_farm_runtime()


def test_production_farm_requires_explicit_enablement_and_loopback_tls(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','production')
    monkeypatch.setenv('CHIEF_SERVICE_CONFIGURED','1')
    with pytest.raises(PermissionError,match='disabled'):
        require_farm_runtime()
    monkeypatch.setenv('CHIEF_FARM_PRODUCTION','ENABLED')
    with pytest.raises(PermissionError,match='TLS proxy'):
        require_farm_runtime()
    monkeypatch.setenv('CHIEF_TRUSTED_PROXY','192.168.1.4')
    with pytest.raises(PermissionError,match='TLS proxy'):
        require_farm_runtime()
    monkeypatch.setenv('CHIEF_TRUSTED_PROXY','127.0.0.1')
    monkeypatch.setenv('DASHBOARD_HOST','192.168.1.8')
    with pytest.raises(PermissionError,match='bind to loopback'):
        require_farm_runtime()
    monkeypatch.setenv('DASHBOARD_HOST','127.0.0.1')
    require_farm_runtime()


def test_unknown_instance_mode_fails_closed(monkeypatch):
    clear(monkeypatch);monkeypatch.setenv('CHIEF_INSTANCE_MODE','staging')
    with pytest.raises(PermissionError,match='supported instance mode'):
        require_farm_runtime()
