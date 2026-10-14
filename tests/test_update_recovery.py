from pathlib import Path

import pytest

from operations import backup
from operations.restore_guard import RestoreQuarantined
from tests.test_operational_backup import fixture, rows
from update_center.recovery import COMPONENTS, guarded_recovery
from worker.ownership import ComponentOwnership, WorkerAlreadyRunning


def arguments(fixture):
    return dict(source_root=fixture.args['source_root'], source_manifest=fixture.args['source_manifest'],
                expected_source=fixture.tree, backup_parent=fixture.root, restore_parent=fixture.root,
                private_storage_confirmed=True)


def test_recovery_preserves_data_and_keeps_locks_until_context_closes(fixture):
    before = rows(fixture.store.path)
    with guarded_recovery(fixture.store.path, **arguments(fixture)) as receipt:
        assert receipt['status'] == 'RECOVERY_DRILL_VERIFIED'
        assert receipt['activation_authorized'] is False and receipt['deployment_certified'] is False
        assert receipt['complete_operational_inventory_verified'] is False
        assert receipt['automatic_replay'] is False
        assert rows(fixture.store.path) == before
        assert rows(Path(receipt['backup_directory']) / 'state.sqlite3') == before
        restored = Path(receipt['isolated_restore_directory']) / 'state.sqlite3'
        assert rows(restored) == before
        with pytest.raises(RestoreQuarantined):
            from database.store import Store
            Store(restored)
        for component in COMPONENTS:
            with pytest.raises(WorkerAlreadyRunning):
                with ComponentOwnership(fixture.store.path, component):
                    pytest.fail('Managed writer acquired a maintenance lock')
    for component in COMPONENTS:
        with ComponentOwnership(fixture.store.path, component):
            pass
    assert receipt['managed_writers_locked'] is False


@pytest.mark.parametrize('component', COMPONENTS)
def test_running_component_prevents_backup_without_stopping_it(fixture, component):
    with ComponentOwnership(fixture.store.path, component):
        with pytest.raises(WorkerAlreadyRunning):
            with guarded_recovery(fixture.store.path, **arguments(fixture)):
                pytest.fail('Maintenance started while a managed writer was running')
    assert not list(fixture.root.glob('update-backup-*'))
    assert not list(fixture.root.glob('update-drill-*'))


@pytest.mark.parametrize('failure', ['unconfirmed', 'wrong-source', 'low-space', 'backup-failure', 'restore-failure'])
def test_recovery_failure_never_yields_activation_authority(fixture, monkeypatch, failure):
    args = arguments(fixture)
    before = rows(fixture.store.path)
    if failure == 'unconfirmed':
        args['private_storage_confirmed'] = False
    elif failure == 'wrong-source':
        args['expected_source'] = 'a' * 64
    else:
        def fail(*a, **kw):
            raise OSError('synthetic failure')
        target = {'low-space': 'update_center.recovery.storage_readiness',
                  'backup-failure': 'operations.backup.create_backup',
                  'restore-failure': 'operations.backup.restore_isolated'}[failure]
        monkeypatch.setattr(target, fail)
    with pytest.raises((ValueError, OSError)):
        with guarded_recovery(fixture.store.path, **args):
            pytest.fail('Failed recovery yielded a receipt')
    assert rows(fixture.store.path) == before
    for component in COMPONENTS:
        with ComponentOwnership(fixture.store.path, component):
            pass


def test_interruption_after_drill_retains_backup_without_replay(fixture):
    with pytest.raises(RuntimeError):
        with guarded_recovery(fixture.store.path, **arguments(fixture)) as receipt:
            raise RuntimeError('synthetic interrupted maintenance')
    assert Path(receipt['backup_directory']).is_dir()
    with pytest.raises(RestoreQuarantined):
        from database.store import Store
        Store(Path(receipt['isolated_restore_directory']) / 'state.sqlite3')
    with ComponentOwnership(fixture.store.path, 'worker'):
        pass


def test_selected_documents_are_verified_but_never_claim_full_coverage(fixture):
    document = fixture.root / 'synthetic-note.txt'
    document.write_text('synthetic non-private note', encoding='utf-8')
    args = arguments(fixture)
    args['assets'] = [dict(path=document, logical_path='notes/synthetic.txt', kind='document')]
    with guarded_recovery(fixture.store.path, **args) as receipt:
        assert receipt['backup']['coverage'] == 'database-and-selected-files'
        assert (Path(receipt['isolated_restore_directory']) / 'assets/notes/synthetic.txt').read_text(encoding='utf-8') == 'synthetic non-private note'
        assert receipt['complete_operational_inventory_verified'] is False


def test_protected_credentials_and_source_destinations_rejected(fixture):
    protected = fixture.root / 'credentials.json'
    protected.write_text('{}', encoding='utf-8')
    args = arguments(fixture)
    args['assets'] = [dict(path=protected, logical_path='configuration.json', kind='configuration')]
    with pytest.raises(backup.BackupError):
        with guarded_recovery(fixture.store.path, **args):
            pytest.fail('Protected credentials copied')
    args = arguments(fixture)
    args['backup_parent'] = args['source_root']
    with pytest.raises(ValueError):
        with guarded_recovery(fixture.store.path, **args):
            pytest.fail('Operational backup entered source directory')
