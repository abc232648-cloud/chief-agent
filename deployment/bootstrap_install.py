"""Thin bootstrap wrapper around the qualified ManagedInstallation provisioner."""
import os
from pathlib import Path

from operations import backup


def bootstrap_installation(directory, *, source_root, source_manifest, expected_source,
                           python, instance_config, schema_sha256,
                           private_storage_confirmed=False):
    """Provision one immutable managed source slot without starting services."""
    from update_center.managed import ManagedInstallation

    root = Path(os.path.abspath(directory))
    source_root = backup._plain(source_root)
    source_manifest = backup._plain(source_manifest, file=True)
    expected_source = backup._sha(expected_source)
    schema_sha256 = backup._sha(schema_sha256)
    instance_config = backup._plain(instance_config, file=True)
    python = Path(os.path.abspath(python))

    actual = backup.source_identity(source_root, source_manifest)
    if actual != expected_source:
        raise ValueError('Bootstrap source does not match the pinned source identity.')

    installation = ManagedInstallation.provision(
        root,
        source_root=source_root,
        source_manifest=source_manifest,
        expected_source=expected_source,
        python=python,
        instance_config=instance_config,
        schema_sha256=schema_sha256,
        private_storage_confirmed=private_storage_confirmed,
    )
    installed_source, installed_manifest = installation.source(expected_source)
    if installation.selection() != expected_source:
        raise ValueError('Managed installation did not select the bootstrap source.')
    return {
        'status': 'MANAGED_INSTALLATION_PROVISIONED',
        'managed_root': str(installation.root),
        'source_tree_sha256': expected_source,
        'installed_source': str(installed_source),
        'installed_manifest': str(installed_manifest),
        'services_started': False,
        'restore_promoted': False,
    }
