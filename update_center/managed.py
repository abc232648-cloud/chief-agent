"""Private immutable source slots and manual, fail-closed version selection.

This module never installs dependencies, migrates data, starts a worker, or
restores a live database. Provisioning the service entrypoint is an operator task.
"""
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

from deployment.service_runtime import atomic_json
from operations import backup
from update_center.release_trust import verify_artifact
from update_center.processes import run as run_process


def _read(path, limit=2 * 1024 * 1024):
    path = backup._plain(path, file=True)
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Managed metadata exceeds its bound.')
    from compatibility.manifest import _unique_object
    return json.loads(raw, object_pairs_hook=_unique_object)


def _write(path, data):
    with Path(path).open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _interpreter(path):
    """Keep the venv entrypoint while pinning its resolved executable.

    Unix virtual environments normally use a final-file symlink. Permit only
    that interpreter link, with plain parents and a separately pinned target;
    this exception never applies to source, configuration or operational data.
    """
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('Absolute interpreter required.')
    backup._plain(path.parent)
    target = backup._plain(path.resolve(strict=True), file=True)
    return path, target


class ManagedInstallation:
    def __init__(self, root):
        self.root = backup._plain(root)
        self.config = _read(self.root / 'installation.json', 16384)
        if set(self.config) != {'version', 'python', 'python_target', 'python_sha256', 'instance_config', 'schema_sha256'} or type(self.config['version']) is not int or self.config['version'] != 1:
            raise ValueError('Invalid managed installation configuration.')
        for field in ('python_sha256', 'schema_sha256'):
            backup._sha(self.config[field])
        for field in ('python_target', 'instance_config'):
            path = Path(self.config[field])
            if not path.is_absolute():
                raise ValueError('Absolute operator paths required.')
            backup._plain(path, file=True)
        _, target = _interpreter(self.config['python'])
        if str(target) != self.config['python_target'] or backup.digest_file(target) != self.config['python_sha256']:
            raise ValueError('Managed interpreter changed; qualify it separately.')
        backup._plain(self.root / 'releases')

    @classmethod
    def provision(cls, root, *, source_root, source_manifest, expected_source, python,
                  instance_config, schema_sha256, private_storage_confirmed=False):
        if private_storage_confirmed is not True:
            raise ValueError('Private installation storage must be confirmed.')
        root = Path(root)
        if not root.is_absolute():
            raise ValueError('Absolute managed installation root required.')
        backup._plain(root.parent)
        backup._sha(schema_sha256)
        if backup.source_identity(source_root, source_manifest) != expected_source:
            raise ValueError('Bootstrap source identity differs.')
        python, python_target = _interpreter(python)
        instance_config = backup._plain(instance_config, file=True)
        root.mkdir(mode=0o700)  # Never adopt or overwrite an existing installation.
        (root / 'releases').mkdir(mode=0o700)
        slot = root / 'releases' / expected_source
        slot.mkdir(mode=0o700)
        source = slot / 'chief-agent'
        source.mkdir(mode=0o700)
        manifest = _read(source_manifest)
        for name in manifest['files']:
            relative = backup._relative(name)
            from compatibility.package import _private
            if _private(relative):
                raise ValueError('Private operational files cannot enter managed source.')
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _write(target, backup._plain(Path(source_root) / relative, file=True).read_bytes())
        _write(slot / 'SOURCE_MANIFEST.json', json.dumps(manifest, sort_keys=True).encode())
        if backup.source_identity(source, slot / 'SOURCE_MANIFEST.json') != expected_source:
            raise ValueError('Copied bootstrap source differs.')
        atomic_json(root / 'installation.json', dict(version=1, python=str(python),
                    python_target=str(python_target), python_sha256=backup.digest_file(python_target), instance_config=str(instance_config), schema_sha256=schema_sha256))
        atomic_json(root / 'active.json', dict(version=1, source_sha256=expected_source))
        atomic_json(root / 'transaction.json', dict(version=1, phase='IDLE', previous=expected_source, candidate=expected_source))
        return cls(root)

    def source(self, identity):
        backup._sha(identity)
        slot = backup._plain(self.root / 'releases' / identity)
        source = backup._plain(slot / 'chief-agent')
        manifest = slot / 'SOURCE_MANIFEST.json'
        if backup.source_identity(source, manifest) != identity:
            raise ValueError('Managed source identity differs.')
        # Extra executable source must not slip through an otherwise valid manifest.
        declared = set(_read(manifest)['files'])
        for path in source.rglob('*'):
            backup._plain(path)
            if path.is_file():
                name = path.relative_to(source).as_posix()
                if name not in declared:
                    raise ValueError('Unlisted file in managed source.')
        return source, manifest

    def selection(self, *, for_stop=False):
        active = _read(self.root / 'active.json', 1024)
        transaction = _read(self.root / 'transaction.json', 4096)
        if set(active) != {'version', 'source_sha256'} or type(active['version']) is not int or active['version'] != 1:
            raise ValueError('Invalid active source pointer.')
        if set(transaction) != {'version', 'phase', 'previous', 'candidate'} or type(transaction['version']) is not int or transaction['version'] != 1:
            raise ValueError('Invalid update transaction.')
        for field in ('previous', 'candidate'):
            backup._sha(transaction[field])
        phase = transaction['phase']
        expected = transaction['candidate'] if phase == 'COMPLETED' else transaction['previous']
        if not for_stop and (phase not in {'IDLE', 'COMPLETED', 'ROLLED_BACK'} or active['source_sha256'] != expected):
            raise PermissionError('Update needs operator recovery before any service can start.')
        self.source(active['source_sha256'])
        return active['source_sha256']

    def stage(self, data, envelope, keys, *, minimum_sequence, now=None):
        release = verify_artifact(data, envelope, keys, minimum_sequence=minimum_sequence, now=now)
        current, _ = self.source(self.selection())
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = json.loads(archive.read('SOURCE_MANIFEST.json'))
            installed_requirements = {p.name: backup.digest_file(p) for p in current.glob('requirements*.txt')}
            incoming_requirements = {n: h for n, h in manifest['files'].items() if '/' not in n and n.startswith('requirements') and n.endswith('.txt')}
            if incoming_requirements != installed_requirements:
                raise ValueError('Dependency changes require a separately qualified runtime; source switching is blocked.')
            slot = self.root / 'releases' / release.manifest.source_tree_sha256
            if slot.exists():
                self.source(release.manifest.source_tree_sha256)
                return release
            slot.mkdir(mode=0o700)
            source = slot / 'chief-agent'
            source.mkdir(mode=0o700)
            for name in manifest['files']:
                target = source / backup._relative(name)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                _write(target, archive.read('chief-agent/' + name))
            _write(slot / 'SOURCE_MANIFEST.json', json.dumps(manifest, sort_keys=True).encode())
            _write(slot / 'envelope.json', envelope)
        self.source(release.manifest.source_tree_sha256)
        return release

    def probe(self, identity, *, timeout=60):
        """Run a fixed read-only Chief preflight, never a command from metadata."""
        if ManagedInstallation(self.root).config != self.config:
            raise ValueError('Managed runtime configuration changed.')
        source, _ = self.source(identity)
        environment = {k: os.environ[k] for k in ('SystemRoot', 'WINDIR', 'LD_LIBRARY_PATH') if k in os.environ}
        environment.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        with tempfile.TemporaryDirectory(prefix='update-probe-', dir=self.root) as scratch:
            environment.update(HOME=scratch, USERPROFILE=scratch, TEMP=scratch, TMP=scratch)
            with tempfile.TemporaryFile(dir=scratch) as output:
                try:
                    result = run_process([self.config['python'], '-B', '-E', '-s', '-m', 'deployment.update_probe',
                        '--config', self.config['instance_config'], '--schema', self.config['schema_sha256']],
                        cwd=source, env=environment, output=output, timeout=timeout)
                except subprocess.TimeoutExpired:
                    return False
                output.seek(0)
                raw = output.read(65537)
                if result.returncode or len(raw) > 65536:
                    return False
                try:
                    value = json.loads(raw)
                except (ValueError, UnicodeError):
                    return False
        self.source(identity)
        return value == {'status': 'READY', 'schema_sha256': self.config['schema_sha256'], 'external_actions': False}

    def _switch(self, candidate, *, expected_current):
        """Called only inside approved, guarded maintenance by the coordinator.

        A crash leaves SWITCHING and blocks managed startup. Ordinary failures
        restore the previous source pointer, never a database snapshot.
        """
        if self.selection() != expected_current or candidate == expected_current:
            raise ValueError('The current release changed or this release is already selected.')
        self.source(candidate)
        transaction = dict(version=1, phase='SWITCHING', previous=expected_current, candidate=candidate)
        atomic_json(self.root / 'transaction.json', transaction)
        try:
            atomic_json(self.root / 'active.json', dict(version=1, source_sha256=candidate))
            if not self.probe(candidate):
                raise ValueError('Selected source failed its read-only startup preflight.')
            atomic_json(self.root / 'transaction.json', dict(transaction, phase='COMPLETED'))
        except Exception:
            self.source(expected_current)
            atomic_json(self.root / 'active.json', dict(version=1, source_sha256=expected_current))
            atomic_json(self.root / 'transaction.json', dict(transaction, phase='ROLLED_BACK'))
            raise
        return {'status': 'SOURCE_SELECTED_RESTART_REQUIRED', 'source_sha256': candidate,
                'previous_source_sha256': expected_current, 'services_started': False, 'data_restored': False}

    def _recover_previous(self, *, expected_previous, expected_candidate):
        """Coordinator-only recovery under reauthorization and maintenance locks.

        Bind recovery to the exact interrupted transaction. Never infer a source
        from directory names or perform operational data restoration.
        """
        self.selection(for_stop=True)  # Validate both records before interpreting them.
        transaction = _read(self.root / 'transaction.json', 4096)
        if transaction != dict(version=1, phase='SWITCHING', previous=expected_previous,
                               candidate=expected_candidate):
            raise ValueError('Recovery does not match an interrupted source switch.')
        active = _read(self.root / 'active.json', 1024)['source_sha256']
        if active not in {expected_previous, expected_candidate}:
            raise ValueError('Active source is unrelated to the interrupted switch.')
        self.source(expected_previous)
        if not self.probe(expected_previous):
            raise ValueError('Previous source failed its read-only startup preflight.')
        atomic_json(self.root / 'active.json', dict(version=1, source_sha256=expected_previous))
        atomic_json(self.root / 'transaction.json', dict(transaction, phase='ROLLED_BACK'))
        return {'status': 'PREVIOUS_SOURCE_SELECTED_RESTART_REQUIRED',
                'source_sha256': expected_previous, 'services_started': False, 'data_restored': False}
