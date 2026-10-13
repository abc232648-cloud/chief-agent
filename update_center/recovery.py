"""Verified recovery preparation while managed Chief writers remain stopped.

No service is stopped or started here. Acquire every existing component lock or
fail; never kill a process or unlock an isolated restore. Successful recovery
preparation is not activation permission or whole-deployment certification.
"""
from contextlib import ExitStack, contextmanager
from pathlib import Path
import uuid

from operations import backup
from operations.restore_guard import assert_not_quarantined, RestoreQuarantined
from operations.storage_health import storage_readiness
from worker.ownership import ComponentOwnership

COMPONENTS = ('dashboard', 'worker', 'scheduler', 'scheduler-cycle', 'approved-action')


@contextmanager
def guarded_recovery(database, *, source_root, source_manifest, expected_source,
                     backup_parent, restore_parent, private_storage_confirmed=False, assets=()):
    """Yield a B.5 recovery receipt while ownership locks remain held.

    The caller must keep this context open throughout any later separately
    authorized maintenance. Closing it releases ownership, so this receipt must
    never be reused as evidence that the current database is still quiescent.
    Assets are explicitly selected non-secret files, not an inferred inventory.
    External/manual writers that do not honor Chief locks must be stopped by
    the installation operator. No assertion about those writers is inferred.
    """
    if private_storage_confirmed is not True:
        raise ValueError('Private backup and isolated restore storage must be confirmed.')
    database = backup._plain(database, file=True)
    source_root = backup._plain(source_root)
    source_manifest = backup._plain(source_manifest, file=True)
    backup_parent, restore_parent = backup._plain(backup_parent), backup._plain(restore_parent)
    backup._sha(expected_source)
    assert_not_quarantined(database)
    for parent in (backup_parent, restore_parent):
        if parent == source_root or parent.is_relative_to(source_root):
            raise ValueError('Operational recovery data must stay outside release source.')
    assets = tuple(assets)
    identity = uuid.uuid4().hex
    destination = backup_parent / ('update-backup-' + identity)
    restore = restore_parent / ('update-drill-' + identity)
    with ExitStack() as stack:
        for component in COMPONENTS:
            stack.enter_context(ComponentOwnership(database, component))
        if backup.source_identity(source_root, source_manifest) != expected_source:
            raise ValueError('Current source differs from the rollback baseline.')
        storage_readiness(backup_parent)
        if restore_parent != backup_parent:
            storage_readiness(restore_parent)
        receipt = backup.create_backup(database, destination, source_root=source_root,
                                       source_manifest=source_manifest, private_storage_confirmed=True,
                                       assets=assets, writers_quiesced=True)
        pins = dict(manifest_sha256=receipt['manifest_sha256'], expected_source=expected_source,
                    expected_schema=receipt['schema_sha256'])
        backup.verify_backup(destination, **pins)
        restored = backup.restore_isolated(destination, restore, **pins, private_storage_confirmed=True)
        # Recheck the baseline and quarantine before yielding any usable receipt.
        if backup.source_identity(source_root, source_manifest) != expected_source:
            raise ValueError('Rollback source changed during recovery preparation.')
        if restored['status'] != 'QUARANTINED' or restored['automatic_replay'] is not False:
            raise ValueError('Isolated restore did not remain quarantined.')
        try:
            assert_not_quarantined(restore / 'state.sqlite3')
        except RestoreQuarantined:
            pass
        else:
            raise ValueError('Isolated restore quarantine is missing.')
        result = {'status': 'RECOVERY_DRILL_VERIFIED', 'backup': receipt,
               'backup_directory': str(destination), 'isolated_restore_directory': str(restore),
               'managed_writers_locked': True, 'locks_valid_only_inside_context': True,
               'restore_status': 'QUARANTINED', 'automatic_replay': False,
               'secrets_and_sessions': 'SEPARATE_PROTECTED_RECOVERY_REQUIRED',
               'complete_operational_inventory_verified': False,
               'deployment_certified': False, 'activation_authorized': False}
        try:
            yield result
        finally:
            result['managed_writers_locked'] = False
