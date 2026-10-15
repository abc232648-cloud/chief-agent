import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from deployment.bootstrap_install import bootstrap_installation
from deployment.source_manifest import build_source_manifest
from operations import backup
from update_center.managed import ManagedInstallation


def _git(root, *args):
    subprocess.run(['git', '-C', str(root), *args], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _repository(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    _git(root, 'init')
    _git(root, 'config', 'user.email', 'chief-tests@example.invalid')
    _git(root, 'config', 'user.name', 'Chief Tests')
    (root / 'app.py').write_text('VALUE = 1\n', encoding='utf-8')
    (root / '.env.example').write_text('SAFE_EXAMPLE=1\n', encoding='utf-8')
    (root / 'state.db').write_bytes(b'private runtime state')
    _git(root, 'add', 'app.py', '.env.example', 'state.db')
    _git(root, 'commit', '-m', 'fixture')
    commit = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], check=True,
                            stdout=subprocess.PIPE).stdout.decode('ascii').strip()
    return root, commit


def test_source_manifest_is_clean_pinned_and_private_state_excluded(tmp_path):
    source, commit = _repository(tmp_path)
    (source / 'untracked.py').write_text('UNTRACKED = True\n', encoding='utf-8')
    output = tmp_path / 'metadata' / 'SOURCE_MANIFEST.json'
    output.parent.mkdir()

    result = build_source_manifest(source, output, expected_git_commit=commit)
    manifest = json.loads(output.read_text(encoding='utf-8'))

    assert result['status'] == 'SOURCE_MANIFEST_BUILT'
    assert result['git_commit'] == commit
    assert set(manifest['files']) == {'.env.example', 'app.py'}
    assert 'state.db' not in manifest['files']
    assert 'untracked.py' not in manifest['files']
    assert result['private_paths_excluded'] == 1
    assert backup.source_identity(source, output) == result['source_tree_sha256']


def test_source_manifest_rejects_dirty_or_wrong_checkout(tmp_path):
    source, commit = _repository(tmp_path)
    output = tmp_path / 'SOURCE_MANIFEST.json'
    with pytest.raises(ValueError, match='pinned release commit'):
        build_source_manifest(source, output, expected_git_commit='0' * 40)

    (source / 'app.py').write_text('VALUE = 2\n', encoding='utf-8')
    with pytest.raises(ValueError, match='local changes'):
        build_source_manifest(source, output, expected_git_commit=commit)


def test_source_manifest_must_live_outside_checkout(tmp_path):
    source, commit = _repository(tmp_path)
    with pytest.raises(ValueError, match='outside'):
        build_source_manifest(source, source / 'SOURCE_MANIFEST.json', expected_git_commit=commit)


def test_bootstrap_wrapper_provisions_exact_immutable_source(tmp_path):
    source = tmp_path / 'payload'
    source.mkdir()
    (source / 'example.py').write_text('VALUE = 1\n', encoding='utf-8')
    digest = hashlib.sha256((source / 'example.py').read_bytes()).hexdigest()
    tree = hashlib.sha256(('example.py\0' + digest).encode()).hexdigest()
    manifest = tmp_path / 'SOURCE_MANIFEST.json'
    manifest.write_text(json.dumps({'files': {'example.py': digest}, 'source_tree_sha256': tree}), encoding='utf-8')
    config = tmp_path / 'instance.json'
    config.write_text('{}', encoding='utf-8')

    result = bootstrap_installation(
        tmp_path / 'managed',
        source_root=source,
        source_manifest=manifest,
        expected_source=tree,
        python=Path(sys.executable),
        instance_config=config,
        schema_sha256='c' * 64,
        private_storage_confirmed=True,
    )

    installation = ManagedInstallation(tmp_path / 'managed')
    installed, installed_manifest = installation.source(tree)
    assert result['status'] == 'MANAGED_INSTALLATION_PROVISIONED'
    assert result['services_started'] is False and result['restore_promoted'] is False
    assert installation.selection() == tree
    assert (installed / 'example.py').read_text(encoding='utf-8') == 'VALUE = 1\n'
    assert backup.source_identity(installed, installed_manifest) == tree


def test_bootstrap_wrapper_requires_private_storage_confirmation(tmp_path):
    source = tmp_path / 'payload'
    source.mkdir()
    (source / 'example.py').write_text('VALUE = 1\n', encoding='utf-8')
    digest = hashlib.sha256((source / 'example.py').read_bytes()).hexdigest()
    tree = hashlib.sha256(('example.py\0' + digest).encode()).hexdigest()
    manifest = tmp_path / 'SOURCE_MANIFEST.json'
    manifest.write_text(json.dumps({'files': {'example.py': digest}, 'source_tree_sha256': tree}), encoding='utf-8')
    config = tmp_path / 'instance.json'
    config.write_text('{}', encoding='utf-8')

    with pytest.raises(ValueError, match='Private installation storage'):
        bootstrap_installation(
            tmp_path / 'managed', source_root=source, source_manifest=manifest,
            expected_source=tree, python=Path(sys.executable), instance_config=config,
            schema_sha256='c' * 64, private_storage_confirmed=False,
        )
