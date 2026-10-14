"""Private immutable source slots and manual, fail-closed version selection.

Source slots remain immutable. Dependency-changing releases use separately
prepared per-release runtimes; source selection never mutates the bootstrap
interpreter, installs packages in place, migrates data, or restores a database.
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
        root.mkdir(mode=0o700)
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
        declared = set(_read(manifest)['files'])
        for path in source.rglob('*'):
            backup._plain(path)
            if path.is_file():
                name = path.relative_to(source).as_posix()
                if name not in declared:
                    raise ValueError('Unlisted file in managed source.')
        return source, manifest

    def _prepared_runtime(self, identity):
        source, _ = self.source(identity)
        path = source.parent / 'runtime.json'
        if not path.exists():
            return None
        value = _read(path, 16384)
        expected = {'version', 'source_sha256', 'requirements_sha256', 'python', 'python_target', 'python_sha256', 'prepared_by'}
        if (not isinstance(value, dict) or set(value) != expected or type(value['version']) is not int or
                value['version'] != 1 or value['source_sha256'] != identity or value['prepared_by'] != 'chief-runtime-v1'):
            raise ValueError('Invalid prepared runtime metadata.')
        backup._sha(value['requirements_sha256']); backup._sha(value['python_sha256'])
        from update_center.runtime_install import requirements_identity
        if requirements_identity(source) != value['requirements_sha256']:
            raise ValueError('Prepared runtime no longer matches release requirements.')
        entry = Path(value['python'])
        runtime_root = source.parent / 'runtime'
        if not entry.is_absolute() or not entry.is_relative_to(runtime_root):
            raise ValueError('Prepared runtime escaped its release slot.')
        entry, target = _interpreter(entry)
        if str(target) != value['python_target'] or backup.digest_file(target) != value['python_sha256']:
            raise ValueError('Prepared runtime interpreter changed.')
        return str(entry)

    def runtime(self, identity):
        """Return a runtime whose requirements match the requested source slot.

        A not-yet-selected candidate inherits from the currently active source
        when their requirement identities match. An active candidate from a
        completed/interrupted switch inherits from that transaction's previous
        source. This preserves a chain of already qualified runtimes across
        sequential source-only releases without falling back to the bootstrap
        environment after a dependency-changing release.
        """
        source, _ = self.source(identity)
        prepared = self._prepared_runtime(identity)
        if prepared is not None:
            return prepared
        from update_center.runtime_install import requirements_identity
        active = _read(self.root / 'active.json', 1024)
        transaction = _read(self.root / 'transaction.json', 4096)
        if (set(active) != {'version', 'source_sha256'} or type(active['version']) is not int or active['version'] != 1 or
                set(transaction) != {'version', 'phase', 'previous', 'candidate'} or
                type(transaction['version']) is not int or transaction['version'] != 1):
            raise ValueError('Invalid managed runtime selection metadata.')
        active_identity = backup._sha(active['source_sha256'])
        previous = backup._sha(transaction['previous'])
        candidate = backup._sha(transaction['candidate'])
        phase = transaction['phase']
        if identity == active_identity:
            if phase in {'SWITCHING', 'COMPLETED', 'ROLLING_BACK_AFTER_START_FAILURE'} and candidate == active_identity and previous != identity:
                reference = previous
            else:
                _interpreter(self.config['python'])
                return self.config['python']
        else:
            reference = active_identity
        reference_source, _ = self.source(reference)
        if requirements_identity(source) == requirements_identity(reference_source):
            return self.runtime(reference)
        raise PermissionError('Candidate dependencies require a prepared isolated runtime.')

    def prepare_runtime(self, identity, *, timeout=1800):
        from update_center.runtime_install import prepare_runtime
        return prepare_runtime(self, identity, timeout=timeout)

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
        self.source(self.selection())
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = json.loads(archive.read('SOURCE_MANIFEST.json'))
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
        """Run a fixed read-only Chief preflight with the qualified release runtime."""
        if ManagedInstallation(self.root).config != self.config:
            raise ValueError('Managed runtime configuration changed.')
        source, _ = self.source(identity)
        python = self.runtime(identity)
        environment = {k: os.environ[k] for k in ('SystemRoot', 'WINDIR', 'LD_LIBRARY_PATH') if k in os.environ}
        environment.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        with tempfile.TemporaryDirectory(prefix='update-probe-', dir=self.root) as scratch:
            environment.update(HOME=scratch, USERPROFILE=scratch, TEMP=scratch, TMP=scratch)
            with tempfile.TemporaryFile(dir=scratch) as output:
                try:
                    result = run_process([python, '-B', '-E', '-s', '-m', 'deployment.update_probe',
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
        if self.selection() != expected_current or candidate == expected_current:
            raise ValueError('The current release changed or this release is already selected.')
        self.source(candidate)
        self.runtime(candidate)
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
        self.selection(for_stop=True)
        transaction = _read(self.root / 'transaction.json', 4096)
        if transaction != dict(version=1, phase='SWITCHING', previous=expected_previous,
                               candidate=expected_candidate):
            raise ValueError('Recovery does not match an interrupted source switch.')
        active = _read(self.root / 'active.json', 1024)['source_sha256']
        if active not in {expected_previous, expected_candidate}:
            raise ValueError('Active source is unrelated to the interrupted switch.')
        self.source(expected_previous)
        self.runtime(expected_previous)
        if not self.probe(expected_previous):
            raise ValueError('Previous source failed its read-only startup preflight.')
        atomic_json(self.root / 'active.json', dict(version=1, source_sha256=expected_previous))
        atomic_json(self.root / 'transaction.json', dict(transaction, phase='ROLLED_BACK'))
        return {'status': 'PREVIOUS_SOURCE_SELECTED_RESTART_REQUIRED',
                'source_sha256': expected_previous, 'services_started': False, 'data_restored': False}

    def _rollback_completed(self, *, expected_previous, expected_candidate):
        """Rollback the exact completed selection after managed startup fails."""
        self.selection(for_stop=True)
        transaction = _read(self.root / 'transaction.json', 4096)
        completed = dict(version=1, phase='COMPLETED', previous=expected_previous, candidate=expected_candidate)
        if transaction != completed:
            raise ValueError('Startup rollback does not match the exact completed transaction.')
        if _read(self.root / 'active.json', 1024) != dict(version=1, source_sha256=expected_candidate):
            raise ValueError('Active source differs from the failed startup candidate.')
        self.source(expected_previous); self.runtime(expected_previous)
        atomic_json(self.root / 'transaction.json', dict(completed, phase='ROLLING_BACK_AFTER_START_FAILURE'))
        try:
            atomic_json(self.root / 'active.json', dict(version=1, source_sha256=expected_previous))
            if not self.probe(expected_previous):
                raise ValueError('Previous source failed its read-only startup preflight.')
            atomic_json(self.root / 'transaction.json', dict(completed, phase='ROLLED_BACK'))
        except Exception:
            atomic_json(self.root / 'active.json', dict(version=1, source_sha256=expected_candidate))
            atomic_json(self.root / 'transaction.json', completed)
            raise
        return {'status': 'PREVIOUS_SOURCE_SELECTED_AFTER_START_FAILURE',
                'source_sha256': expected_previous, 'failed_source_sha256': expected_candidate,
                'services_started': False, 'data_restored': False}
