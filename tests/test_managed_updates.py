from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

from deployment.service_runtime import atomic_json
from operations import backup
from tests.test_update_release_trust import NOW, release
from tests.test_update_staging import package
from update_center.managed import ManagedInstallation


PROBE = b'''import argparse,json
p=argparse.ArgumentParser();p.add_argument('--config');p.add_argument('--schema');a=p.parse_args()
print(json.dumps(dict(status='READY',schema_sha256=a.schema,external_actions=False)))
'''


@pytest.fixture
def managed(tmp_path, release):
    files = {'deployment/__init__.py': b'', 'deployment/update_probe.py': PROBE,
             'example.py': b'VERSION=1\n', 'requirements.txt': b'# synthetic unchanged environment\n'}
    source = tmp_path / 'baseline'
    source.mkdir()
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    hashes = {n: hashlib.sha256(v).hexdigest() for n, v in files.items()}
    tree = hashlib.sha256('\n'.join(n+'\0'+hashes[n] for n in sorted(hashes)).encode()).hexdigest()
    manifest = tmp_path / 'baseline.json'
    manifest.write_text(json.dumps(dict(files=hashes, source_tree_sha256=tree)), encoding='utf-8')
    config = tmp_path / 'instance.json'
    config.write_text('{}', encoding='utf-8')
    installation = ManagedInstallation.provision(tmp_path / 'managed', source_root=source,
        source_manifest=manifest, expected_source=tree, python=Path(sys.executable),
        instance_config=config, schema_sha256='c'*64, private_storage_confirmed=True)
    key, payload, sign, _ = release
    def candidate(changes=None):
        changed = {**files, 'example.py': b'VERSION=2\n', **(changes or {})}
        data, pins = package(changed)
        value = deepcopy(payload)
        value.update(archive_sha256=pins['expected_archive'], archive_size=len(data))
        value['manifest']['source_tree_sha256'] = pins['expected_source']
        return data, sign(value), [key], pins['expected_source']
    return installation, tree, candidate


def test_stage_probe_and_manual_selection_leave_baseline_intact(managed):
    installation, previous, candidate = managed
    data, envelope, keys, target = candidate()
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    assert installation.selection() == previous
    assert installation.probe(target)
    result = installation._switch(target, expected_current=previous)
    assert result['status'] == 'SOURCE_SELECTED_RESTART_REQUIRED'
    assert result['services_started'] is False and result['data_restored'] is False
    assert installation.selection() == target
    baseline, _ = installation.source(previous)
    assert (baseline / 'example.py').read_bytes() == b'VERSION=1\n'


def test_failed_post_selection_probe_rolls_back_source_only(managed):
    installation, previous, candidate = managed
    data, envelope, keys, target = candidate({'deployment/update_probe.py': b'raise SystemExit(3)\n'})
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    with pytest.raises(ValueError):
        installation._switch(target, expected_current=previous)
    assert installation.selection() == previous
    assert json.loads((installation.root / 'transaction.json').read_bytes())['phase'] == 'ROLLED_BACK'


def test_interrupted_switch_blocks_every_managed_startup(managed, monkeypatch):
    installation, previous, candidate = managed
    data, envelope, keys, target = candidate()
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    def interrupted(*a, **kw):
        raise KeyboardInterrupt()
    monkeypatch.setattr(installation, 'probe', interrupted)
    with pytest.raises(KeyboardInterrupt):
        installation._switch(target, expected_current=previous)
    with pytest.raises(PermissionError):
        installation.selection()
    assert installation.selection(for_stop=True) == target


def test_dependency_change_stages_but_requires_qualified_runtime(managed):
    installation, previous, candidate = managed
    data, envelope, keys, target = candidate({'requirements.txt': b'synthetic-new-dependency==1\n'})
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    assert installation.selection() == previous
    with pytest.raises(PermissionError, match='prepared isolated runtime'):
        installation.runtime(target)
    with pytest.raises(PermissionError, match='prepared isolated runtime'):
        installation._switch(target, expected_current=previous)
    assert installation.selection() == previous


def test_source_tampering_remains_blocked(managed):
    installation, previous, _ = managed
    source, _ = installation.source(previous)
    (source / 'injected.py').write_text('pass', encoding='utf-8')
    with pytest.raises(ValueError, match='Unlisted'):
        installation.selection()


def test_wrong_baseline_and_duplicate_selection_rejected(managed):
    installation, previous, candidate = managed
    data, envelope, keys, target = candidate()
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    with pytest.raises(ValueError):
        installation._switch(target, expected_current='a'*64)
    with pytest.raises(ValueError):
        installation._switch(previous, expected_current=previous)


def test_probe_does_not_inherit_provider_secrets(managed, monkeypatch):
    installation, _, candidate = managed
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-sensitive-sentinel')
    guarded = b'import os\nassert "GROQ_API_KEY" not in os.environ\n' + PROBE
    data, envelope, keys, target = candidate({'deployment/update_probe.py': guarded})
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    assert installation.probe(target)


def test_source_change_after_stage_rejected_before_pointer_write(managed):
    installation, previous, candidate = managed
    data, envelope, keys, target = candidate()
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    source, _ = installation.source(target)
    (source / 'example.py').write_text('changed', encoding='utf-8')
    with pytest.raises(backup.BackupError):
        installation._switch(target, expected_current=previous)
    assert installation.selection() == previous


def test_unlisted_bytecode_is_not_exempt_from_source_integrity(managed):
    installation, previous, _ = managed
    source, _ = installation.source(previous)
    cache = source / '__pycache__'
    cache.mkdir()
    (cache / 'example.cpython-312.pyc').write_bytes(b'untrusted executable cache')
    with pytest.raises(ValueError, match='Unlisted'):
        installation.selection()


def test_interrupted_recovery_requires_exact_transaction_and_keeps_data(managed, monkeypatch):
    installation, previous, candidate = managed
    data, envelope, keys, target = candidate()
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    original = installation.probe
    monkeypatch.setattr(installation, 'probe', lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        installation._switch(target, expected_current=previous)
    monkeypatch.setattr(installation, 'probe', original)
    config = Path(installation.config['instance_config'])
    before = config.read_bytes()
    with pytest.raises(ValueError):
        installation._recover_previous(expected_previous=previous, expected_candidate='a'*64)
    result = installation._recover_previous(expected_previous=previous, expected_candidate=target)
    assert result['data_restored'] is False and result['services_started'] is False
    assert installation.selection() == previous and config.read_bytes() == before
    with pytest.raises(ValueError):
        installation._recover_previous(expected_previous=previous, expected_candidate=target)


def test_recovery_probe_failure_preserves_startup_block(managed, monkeypatch):
    installation, previous, candidate = managed
    data, envelope, keys, target = candidate()
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    atomic_json(installation.root / 'transaction.json', dict(version=1, phase='SWITCHING', previous=previous, candidate=target))
    monkeypatch.setattr(installation, 'probe', lambda *a: False)
    with pytest.raises(ValueError):
        installation._recover_previous(expected_previous=previous, expected_candidate=target)
    with pytest.raises(PermissionError):
        installation.selection()


def test_completed_activation_can_roll_back_exact_source_without_data_restore(managed):
    installation, previous, candidate = managed
    data, envelope, keys, target = candidate()
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    installation._switch(target, expected_current=previous)
    config = Path(installation.config['instance_config'])
    before = config.read_bytes()
    result = installation._rollback_completed(expected_previous=previous, expected_candidate=target)
    assert result['status'] == 'PREVIOUS_SOURCE_SELECTED_AFTER_START_FAILURE'
    assert result['data_restored'] is False and installation.selection() == previous
    assert config.read_bytes() == before
    with pytest.raises(ValueError):
        installation._rollback_completed(expected_previous=previous, expected_candidate=target)


@pytest.mark.parametrize('component', ['dashboard', 'worker', 'scheduler', 'scheduler-cycle', 'approved-action'])
def test_stale_managed_startup_releases_lock_without_claiming_ownership(managed, monkeypatch, tmp_path, component):
    from worker.ownership import ComponentOwnership
    installation, previous, _ = managed
    monkeypatch.setenv('CHIEF_MANAGED_INSTALLATION', str(installation.root))
    monkeypatch.setenv('CHIEF_MANAGED_SOURCE', previous)
    database = tmp_path / 'synthetic.sqlite3'
    owner = ComponentOwnership(database, component)
    with pytest.raises(PermissionError, match='selection changed'):
        with owner:
            pytest.fail('Stale process acquired managed ownership')
    assert owner.path.read_bytes() == b''
    monkeypatch.delenv('CHIEF_MANAGED_INSTALLATION')
    monkeypatch.delenv('CHIEF_MANAGED_SOURCE')
    with ComponentOwnership(database, component):
        pass
