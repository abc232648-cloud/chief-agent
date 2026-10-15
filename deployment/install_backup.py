"""Create a database-only installation backup pinned to an exact source manifest."""
import os
from pathlib import Path


def create_install_backup(config, destination, *, source_root, source_manifest,
                          private_storage_confirmed=False):
    """Create a new immutable backup receipt for installation readiness.

    This does not migrate, restore or start services. SQLite's backup API captures
    the database consistently; operational files, credentials and sessions remain
    excluded and are reprovisioned separately by their existing mechanisms.
    """
    before = dict(os.environ)
    try:
        from deployment.launch import configure
        instance = configure(str(Path(config).absolute()))
        if instance.mode != 'production':
            raise PermissionError('Installation backup requires a prepared production instance.')
        from operations.backup import create_backup
        receipt = create_backup(
            instance.database,
            destination,
            source_root=source_root,
            source_manifest=source_manifest,
            private_storage_confirmed=private_storage_confirmed,
            writers_quiesced=False,
        )
        if receipt['schema_sha256'] != instance.schema_sha256:
            raise ValueError('Installation backup schema differs from the prepared production instance.')
        return {
            'status': 'INSTALLATION_BACKUP_CREATED',
            **receipt,
            'backup_directory': str(Path(destination).absolute()),
            'operational_files_included': False,
            'restore_promoted': False,
        }
    finally:
        os.environ.clear()
        os.environ.update(before)
