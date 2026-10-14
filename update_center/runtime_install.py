"""Prepare isolated per-release Python runtimes for dependency-changing updates.

The source package is publisher-verified before this module is called. Automatic
dependency changes additionally require a signed, hash-locked wheel requirements
file. The bootstrap interpreter and operational state are never mutated.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile

from deployment.service_runtime import atomic_json
from operations import backup
from update_center.processes import run as run_process

_LOCK = 'requirements-lock.txt'
_PRIMARY = 'requirements.txt'
_MAX_LOCK = 2 * 1024 * 1024
_PACKAGE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9.!+_-]+(?:\s*;[^\\]+?)?(?:\s+--hash=sha256:[0-9a-f]{64})+$')
_HASH = re.compile(r'--hash=sha256:[0-9a-f]{64}')


def requirements_identity(source):
    """Identity only files that define the production managed runtime."""
    source = backup._plain(source)
    primary = backup._plain(source / _PRIMARY, file=True)
    rows = [_PRIMARY + '\0' + backup.digest_file(primary)]
    lock = source / _LOCK
    if lock.exists() or lock.is_symlink():
        lock = backup._plain(lock, file=True)
        rows.append(_LOCK + '\0' + backup.digest_file(lock))
    return hashlib.sha256('\n'.join(rows).encode()).hexdigest()


def _validate_lock(source):
    """Reject requirements-file directives, URLs, editable refs and unhashed pins."""
    path = backup._plain(source / _LOCK, file=True)
    raw = path.read_bytes()
    if len(raw) > _MAX_LOCK:
        raise ValueError('Dependency lock exceeds its size limit.')
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('Dependency lock must be UTF-8.') from exc
    logical, pending = [], ''
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        pending = (pending + ' ' + line).strip()
        if pending.endswith('\\'):
            pending = pending[:-1].rstrip()
            continue
        logical.append(' '.join(pending.split()))
        pending = ''
    if pending:
        raise ValueError('Dependency lock has an incomplete continuation.')
    if not logical:
        raise ValueError('Dependency-changing release requires a non-empty dependency lock.')
    for line in logical:
        lowered = line.lower()
        if ('://' in line or ' @ ' in line or line.startswith('-') or
                '--index-url' in lowered or '--extra-index-url' in lowered or
                '--find-links' in lowered or '--trusted-host' in lowered or
                '--editable' in lowered or '--requirement' in lowered):
            raise ValueError('Dependency lock contains an unsupported source or directive.')
        hashes = _HASH.findall(line)
        if not hashes or len(hashes) != len(set(hashes)) or not _PACKAGE.fullmatch(line):
            raise ValueError('Dependency lock must contain exact SHA-256-hashed package pins only.')
    return path


def _runtime_python(root):
    return root / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def _run(command, *, cwd, environment, output, timeout):
    result = run_process(command, cwd=cwd, env=environment, output=output, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Dependency runtime preparation failed; inspect the private install log.')


def prepare_runtime(installation, identity, *, timeout=1800):
    """Prepare a candidate runtime only when its production dependency identity changed."""
    if type(timeout) is not int or not 60 <= timeout <= 7200:
        raise ValueError('Dependency installation timeout must be between 60 and 7200 seconds.')
    source, _ = installation.source(identity)
    current = installation.selection()
    if identity == current:
        return {'status': 'CURRENT_RUNTIME_ALREADY_SELECTED', 'python': installation.runtime(identity)}
    current_source, _ = installation.source(current)
    candidate_requirements = requirements_identity(source)
    current_requirements = requirements_identity(current_source)
    if candidate_requirements == current_requirements:
        return {'status': 'DEPENDENCIES_UNCHANGED', 'python': installation.runtime(current),
                'requirements_sha256': candidate_requirements}
    lock = _validate_lock(source)
    slot = backup._plain(source.parent)
    metadata_path = slot / 'runtime.json'
    if metadata_path.exists():
        return {'status': 'RUNTIME_ALREADY_PREPARED', 'python': installation.runtime(identity),
                'requirements_sha256': candidate_requirements}
    runtime = slot / 'runtime'
    if runtime.exists() or runtime.is_symlink():
        raise PermissionError('Incomplete candidate runtime requires operator review before retry.')
    runtime.mkdir(mode=0o700)
    log = slot / 'runtime-install.log'
    environment = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'LD_LIBRARY_PATH') if key in os.environ}
    environment.update(PYTHONUTF8='1', PIP_DISABLE_PIP_VERSION_CHECK='1', PYTHONDONTWRITEBYTECODE='1')
    with tempfile.TemporaryDirectory(prefix='runtime-home-', dir=slot) as scratch:
        environment.update(HOME=scratch, USERPROFILE=scratch, TEMP=scratch, TMP=scratch)
        with log.open('xb') as output:
            os.chmod(log, 0o600)
            _run([installation.config['python'], '-I', '-m', 'venv', str(runtime)],
                 cwd=source, environment=environment, output=output, timeout=timeout)
            python = _runtime_python(runtime)
            backup._plain(python.parent)
            _run([str(python), '-I', '-m', 'pip', 'install', '--isolated', '--disable-pip-version-check',
                  '--no-input', '--require-hashes', '--only-binary=:all:', '--index-url',
                  'https://pypi.org/simple', '--requirement', str(lock)],
                 cwd=source, environment=environment, output=output, timeout=timeout)
            _run([str(python), '-I', '-m', 'pip', 'check'], cwd=source,
                 environment=environment, output=output, timeout=min(timeout, 600))
    entry = Path(python)
    target = backup._plain(entry.resolve(strict=True), file=True)
    atomic_json(metadata_path, {
        'version': 1,
        'source_sha256': identity,
        'requirements_sha256': candidate_requirements,
        'python': str(entry),
        'python_target': str(target),
        'python_sha256': backup.digest_file(target),
        'prepared_by': 'chief-runtime-v1',
    })
    verified = installation.runtime(identity)
    return {'status': 'RUNTIME_PREPARED', 'python': verified,
            'requirements_sha256': candidate_requirements, 'log': str(log)}
