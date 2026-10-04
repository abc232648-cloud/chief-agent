import hashlib
import json

import pytest

from browser.runtime import launch_options


def selection(tmp_path, monkeypatch):
    executable = tmp_path / 'synthetic-browser'
    executable.write_bytes(b'synthetic executable identity; never launched')
    config = tmp_path / 'browser.json'
    data = {'executable': str(executable), 'sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
            'version': '154.0.8037.57'}
    config.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setenv('CHIEF_BROWSER_RUNTIME', str(config))
    return config, executable, data


def test_production_requires_explicit_qualified_runtime(monkeypatch):
    monkeypatch.delenv('CHIEF_BROWSER_RUNTIME', raising=False)
    monkeypatch.setenv('CHIEF_INSTANCE_MODE', 'production')
    with pytest.raises(PermissionError, match='qualified browser'):
        launch_options()


def test_explicit_runtime_always_requires_native_sandbox(tmp_path, monkeypatch):
    _, executable, _ = selection(tmp_path, monkeypatch)
    assert launch_options() == {'executable_path': str(executable), 'chromium_sandbox': True}


def test_changed_runtime_is_rejected_without_fallback(tmp_path, monkeypatch):
    _, executable, _ = selection(tmp_path, monkeypatch)
    executable.write_bytes(b'changed executable')
    with pytest.raises(PermissionError, match='requalification'):
        launch_options()


@pytest.mark.parametrize('change', [
    {'executable': 'relative/browser'}, {'sha256': 'unverified'}, {'version': 'latest'},
    {'chromium_sandbox': False},
])
def test_invalid_runtime_configuration_is_rejected(tmp_path, monkeypatch, change):
    config, _, data = selection(tmp_path, monkeypatch)
    data.update(change)
    config.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError):
        launch_options()


def test_runtime_configuration_is_bounded_and_absolute(tmp_path, monkeypatch):
    config, _, _ = selection(tmp_path, monkeypatch)
    config.write_text(' ' * 8193, encoding='utf-8')
    with pytest.raises(ValueError, match='limit'):
        launch_options()
    monkeypatch.setenv('CHIEF_BROWSER_RUNTIME', 'browser.json')
    with pytest.raises(ValueError, match='absolute'):
        launch_options()
