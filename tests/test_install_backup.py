from pathlib import Path
from types import SimpleNamespace

import pytest

from deployment.install_backup import create_install_backup


def test_install_backup_pins_source_and_schema_without_restore(tmp_path, monkeypatch):
    database = tmp_path / 'state.sqlite3'
    database.write_bytes(b'synthetic')
    config = tmp_path / 'instance.json'
    config.write_text('{}', encoding='utf-8')
    source = tmp_path / 'source'
    source.mkdir()
    manifest = tmp_path / 'SOURCE_MANIFEST.json'
    manifest.write_text('{}', encoding='utf-8')
    destination = tmp_path / 'backup'
    schema = 'b' * 64
    source_sha = 'a' * 64

    import deployment.launch
    import operations.backup
    monkeypatch.setattr(deployment.launch, 'configure', lambda filename: SimpleNamespace(
        mode='production', database=database, schema_sha256=schema))

    captured = {}
    def fake_backup(db, dest, **kwargs):
        captured.update(database=db, destination=dest, **kwargs)
        return {'backup_id': 'synthetic', 'manifest_sha256': 'c' * 64,
                'source_tree_sha256': source_sha, 'schema_sha256': schema,
                'coverage': 'database-only'}
    monkeypatch.setattr(operations.backup, 'create_backup', fake_backup)

    result = create_install_backup(config, destination, source_root=source,
                                   source_manifest=manifest,
                                   private_storage_confirmed=True)

    assert result['status'] == 'INSTALLATION_BACKUP_CREATED'
    assert result['source_tree_sha256'] == source_sha
    assert result['schema_sha256'] == schema
    assert result['operational_files_included'] is False
    assert result['restore_promoted'] is False
    assert captured['database'] == database
    assert captured['source_root'] == source
    assert captured['source_manifest'] == manifest
    assert captured['private_storage_confirmed'] is True
    assert captured['writers_quiesced'] is False


def test_install_backup_rejects_nonproduction_instance(tmp_path, monkeypatch):
    config = tmp_path / 'instance.json'
    config.write_text('{}', encoding='utf-8')
    import deployment.launch
    monkeypatch.setattr(deployment.launch, 'configure', lambda filename: SimpleNamespace(
        mode='test', database=tmp_path / 'state.sqlite3', schema_sha256='b' * 64))
    with pytest.raises(PermissionError, match='production'):
        create_install_backup(config, tmp_path / 'backup', source_root=tmp_path,
                              source_manifest=config, private_storage_confirmed=True)
