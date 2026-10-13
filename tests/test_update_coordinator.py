import base64
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
import json
import os

import pytest

from application.composition import default_catalogs
from browser.site_access import SiteAccess
from control.notifications import initialize
from database.store import Store
from deployment.schema import fingerprint
from deployment.service_runtime import atomic_json
from operations.time_integrity import utc_now, utc_text
from policy.engine import PolicyEngine
from tests.checkpoint_f_fixture import secure_store
from tests.test_managed_updates import managed
from tests.test_operational_backup import rows
from tests.test_update_center import current, candidate
from tests.test_update_release_trust import release
from tests.test_update_staging import package
from application.update_coordinator import ManualUpdater
from update_center.managed import ManagedInstallation
from update_center.planner import plan_update
from worker.ownership import ComponentOwnership, WorkerAlreadyRunning


@pytest.fixture
def coordinator(managed, release, tmp_path, monkeypatch):
    installation, previous, _ = managed
    store = Store(tmp_path / 'synthetic.db'); initialize(store); SiteAccess(store)
    _, owner = secure_store(store)
    store.queue_command('Synthetic queue; never replay during update')
    action = store.add_action('Synthetic approval', 'fixture'); store.resolve_action(action, 'APPROVED')
    with store._connect() as con:
        schema = fingerprint(con)
    atomic_json(installation.root / 'installation.json', {**installation.config, 'schema_sha256': schema})
    atomic_json(installation.config['instance_config'], dict(mode='ISOLATED_DEVELOPMENT', state_root=str(tmp_path),
        database=str(store.path), schema_sha256=schema))
    installation = ManagedInstallation(installation.root)
    baseline = replace(current(), source_tree_sha256=previous, schema_sha256=schema)
    proposed = replace(candidate(), schema_sha256=schema, changed_nodes=('component:chief.update-center',),
                       required_tests=('tests/test_synthetic.py',))
    catalog = default_catalogs().capabilities
    required = plan_update(baseline, proposed, catalog).required_tests
    root, manifest = installation.source(previous)
    files = {name:(root/name).read_bytes() for name in json.loads(manifest.read_bytes())['files']}
    # Synthetic code proves orchestration only; not a production regression pass.
    files.update({name:b'def test_synthetic_fixture():\n    assert True\n' for name in required})
    files['example.py'] = b'VERSION=2\n'
    data, pins = package(files)
    key, original, sign, _ = release
    payload = deepcopy(original)
    metadata = asdict(replace(proposed, source_tree_sha256=pins['expected_source']))
    metadata['capability_versions'] = [dict(id=k, version=v) for k,v in proposed.capability_versions]
    payload.update(manifest=metadata, archive_sha256=pins['expected_archive'], archive_size=len(data),
        issued_at=utc_text(utc_now()-timedelta(minutes=1)), expires_at=utc_text(utc_now()+timedelta(hours=1)))
    installation.stage(data, sign(payload), [key], minimum_sequence=1)
    policy_file = tmp_path / 'publisher.json'
    atomic_json(policy_file, dict(version=1, repository=payload['repository'], minimum_sequence=1,
        keys=[dict(key_id=key.key_id, public_key=base64.b64encode(key.public_key).decode(),
                   valid_from=key.valid_from, valid_until=key.valid_until, revoked=False)]))
    monkeypatch.setattr(os, 'environ', dict(os.environ))
    for name in ('JOB_WORKER_DB', 'CHIEF_STATE_ROOT', 'DASHBOARD_HOST'):
        os.environ.pop(name, None)
    updater = ManualUpdater(installation, baseline, catalog, PolicyEngine(()), trust_policy=policy_file,
        evidence_parent=tmp_path, backup_parent=tmp_path, restore_parent=tmp_path)
    return updater, owner, pins['expected_source'], store, policy_file


def validated_review(coordinator):
    updater, owner, candidate, _, _ = coordinator
    result = updater.validate(owner, candidate, confirm_reviewed_code=True)
    assert result['status'] == 'PASSED'
    return updater.approve(owner, candidate, result['run_id'])


def test_manual_lifecycle_preserves_domain_data_and_retains_quarantine(coordinator, tmp_path):
    updater, owner, candidate, store, _ = coordinator
    decision = validated_review(coordinator)
    before = rows(store.path)
    result = updater.select(owner, candidate, decision['id'], private_storage_confirmed=True)
    assert result['status'] == 'SOURCE_SELECTED_RESTART_REQUIRED'
    assert result['services_started'] is False and result['data_restored'] is False
    assert result['restore_status'] == 'QUARANTINED' and result['deployment_certified'] is False
    after = rows(store.path)
    assert {k:v for k,v in before.items() if k not in {'control_state', 'human_security_events'}} == {
        k:v for k,v in after.items() if k not in {'control_state', 'human_security_events'}}
    assert updater.installation.selection() == candidate
    from operations.restore_guard import assert_not_quarantined, RestoreQuarantined
    restored = list(tmp_path.glob('update-drill-*/state.sqlite3'))
    assert len(restored) == 1
    with pytest.raises(RestoreQuarantined):
        assert_not_quarantined(restored[0])
    with pytest.raises(ValueError):
        updater.select(owner, candidate, decision['id'], private_storage_confirmed=True)


def test_live_component_prevents_selection_without_stopping_it(coordinator, tmp_path):
    updater, owner, candidate, store, _ = coordinator
    decision = validated_review(coordinator)
    with ComponentOwnership(store.path, 'worker'):
        with pytest.raises(WorkerAlreadyRunning):
            updater.select(owner, candidate, decision['id'], private_storage_confirmed=True)
    assert updater.installation.selection() == updater.current.source_tree_sha256
    assert not list(tmp_path.glob('update-backup-*'))


def test_publisher_revocation_after_review_blocks_selection(coordinator, tmp_path):
    updater, owner, candidate, _, policy = coordinator
    decision = validated_review(coordinator)
    value = json.loads(policy.read_bytes()); value['keys'][0]['revoked'] = True; atomic_json(policy, value)
    with pytest.raises(ValueError):
        updater.select(owner, candidate, decision['id'], private_storage_confirmed=True)
    assert updater.installation.selection() == updater.current.source_tree_sha256
    assert not list(tmp_path.glob('update-backup-*'))


def test_missing_server_evidence_cannot_be_reviewed(coordinator):
    updater, owner, candidate, _, _ = coordinator
    with pytest.raises(ValueError):
        updater.approve(owner, candidate, 'not-a-real-test-run')


def test_updater_storage_cannot_modify_operational_records(coordinator):
    import sqlite3
    updater, _, _, _, _ = coordinator
    with updater.store._connect() as con:
        for statement in ('DELETE FROM commands', "UPDATE actions SET status='APPROVED'", 'CREATE TABLE forbidden(id INTEGER)',
                          'UPDATE human_sessions SET revoked=0', 'DELETE FROM human_security_events'):
            with pytest.raises(sqlite3.DatabaseError):
                con.execute(statement)


def test_interrupted_selection_needs_new_explicit_recovery_and_never_restores_data(coordinator, monkeypatch):
    updater, owner, candidate, store, _ = coordinator
    decision = validated_review(coordinator)
    probe = updater.installation.probe
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(updater.installation, 'probe', interrupted)
    with pytest.raises(KeyboardInterrupt):
        updater.select(owner, candidate, decision['id'], private_storage_confirmed=True)
    with pytest.raises(PermissionError):
        updater.installation.selection()
    monkeypatch.setattr(updater.installation, 'probe', probe)
    with pytest.raises(PermissionError):
        updater.recover_previous(owner, candidate, private_storage_confirmed=True)
    with pytest.raises(ValueError):
        updater.recover_previous(owner, 'f'*64, confirm_recovery=True, private_storage_confirmed=True)
    before = rows(store.path)['actions']
    result = updater.recover_previous(owner, candidate, confirm_recovery=True, private_storage_confirmed=True)
    assert result['status'] == 'PREVIOUS_SOURCE_SELECTED_RESTART_REQUIRED'
    assert result['data_restored'] is False and result['services_started'] is False
    assert rows(store.path)['actions'] == before
    assert updater.installation.selection() == updater.current.source_tree_sha256
