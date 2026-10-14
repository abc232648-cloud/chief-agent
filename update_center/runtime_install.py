"""Prepare isolated per-release Python runtimes for dependency-changing updates.

The source package is already publisher-verified before this module is called.
Dependencies are installed into a new release-local virtual environment; the
bootstrap interpreter and operational state are never mutated.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from deployment.service_runtime import atomic_json
from operations import backup
from update_center.processes import run as run_process


def requirements_identity(source):
    source = backup._plain(source)
    rows = []
    for path in sorted(source.glob('requirements*.txt'), key=lambda item: item.name):
        plain = backup._plain(path, file=True)
        rows.append(path.name + '\0' + backup.digest_file(plain))
    if not rows:
        raise ValueError('Managed release has no root requirements file.')
    return hashlib.sha256('\n'.join(rows).encode()).hexdigest()


def _runtime_python(root):
    return root / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def _run(command, *, cwd, environment, output, timeout):
    result = run_process(command, cwd=cwd, env=environment, output=output, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Dependency runtime preparation failed; inspect the private install log.')


def prepare_runtime(installation, identity, *, timeout=1800):
    """Prepare a candidate runtime only when its dependency identity changed."""
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
                  '--no-input', '--index-url', 'https://pypi.org/simple', '--requirement', str(source / 'requirements.txt')],
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
