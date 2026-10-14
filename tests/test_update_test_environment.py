import hashlib
import json
from pathlib import Path
import sys

import pytest

from update_center.test_environment import resolve


@pytest.fixture
def inputs(tmp_path):
    # Identity-only synthetic descriptor; this does not qualify a real browser.
    executable = Path(sys.executable).resolve()
    runtime = tmp_path / 'browser.json'
    runtime.write_text(json.dumps(dict(executable=str(executable), sha256=hashlib.sha256(executable.read_bytes()).hexdigest(), version='1.0.0.0')), encoding='utf-8')
    clients = tmp_path / 'clients'; clients.mkdir()
    for name in ('chief-owner-pwa', 'chief-staff-pwa', 'Job-agent-pwa'):
        dist = clients / name / 'dist'; dist.mkdir(parents=True)
        (dist / 'index.html').write_text('<p>Synthetic client</p>', encoding='utf-8')
    config = tmp_path / 'test.json'
    config.write_text(json.dumps(dict(browser_runtime=str(runtime), client_root=str(clients))), encoding='utf-8')
    return config, runtime, clients


def test_only_explicit_inputs_pass_and_client_changes_invalidate_identity(inputs):
    config, _, clients = inputs
    environment, before = resolve(str(config))
    assert set(environment) == {'CHIEF_BROWSER_RUNTIME','CHIEF_A10_CLIENT_ROOT','CHIEF_A10_BROWSER'}
    assert environment['CHIEF_A10_BROWSER'] == 'chromium'
    (clients / 'chief-staff-pwa/dist/index.html').write_text('changed', encoding='utf-8')
    assert resolve(str(config))[1] != before


def test_arbitrary_environment_and_changed_browser_are_rejected(inputs):
    config, runtime, _ = inputs
    original = config.read_bytes()
    value = json.loads(original); value['GROQ_API_KEY'] = 'synthetic-secret'
    config.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError):resolve(str(config))
    config.write_bytes(original)
    value = json.loads(runtime.read_bytes()); value['sha256'] = '0'*64
    runtime.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(PermissionError):resolve(str(config))


def test_missing_pwa_build_is_not_silently_substituted(inputs):
    config, _, clients = inputs
    (clients / 'Job-agent-pwa/dist/index.html').unlink()
    with pytest.raises(FileNotFoundError):resolve(str(config))
