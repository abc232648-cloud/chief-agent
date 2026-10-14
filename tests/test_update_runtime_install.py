import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.test_managed_updates import managed
from tests.test_update_release_trust import NOW

LOCK = b'synthetic-new-dependency==1 --hash=sha256:' + b'a' * 64 + b'\n'


def _fake_runtime_runner(calls):
    def fake_run(command, *, cwd, env, output, timeout, max_output_bytes=8*1024*1024):
        calls.append(tuple(command))
        if '-m' in command and 'venv' in command:
            runtime = Path(command[-1])
            python = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            python.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sys.executable, python)
        return subprocess.CompletedProcess(command, 0)
    return fake_run


def test_dependency_change_requires_signed_hash_lock(managed):
    installation, _, candidate = managed
    data, envelope, keys, target = candidate({'requirements.txt': b'synthetic-new-dependency==1\n'})
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    with pytest.raises((FileNotFoundError, ValueError)):
        installation.prepare_runtime(target, timeout=60)


def test_dependency_change_prepares_release_local_runtime(managed, monkeypatch):
    installation, previous, candidate = managed
    data, envelope, keys, target = candidate({
        'requirements.txt': b'synthetic-new-dependency==1\n',
        'requirements-lock.txt': LOCK,
    })
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)

    calls = []
    monkeypatch.setattr('update_center.runtime_install.run_process', _fake_runtime_runner(calls))
    result = installation.prepare_runtime(target, timeout=60)
    assert result['status'] == 'RUNTIME_PREPARED'
    assert Path(result['python']).is_relative_to(installation.root / 'releases' / target / 'runtime')
    assert installation.runtime(target) == result['python']
    assert installation.selection() == previous
    install = next(command for command in calls if 'pip' in command and 'install' in command)
    assert '--require-hashes' in install and '--only-binary=:all:' in install
    assert install[install.index('--index-url') + 1] == 'https://pypi.org/simple'
    assert install[-1].endswith('requirements-lock.txt')
    assert any('venv' in command for command in calls)
    assert any('pip' in command and 'check' in command for command in calls)


def test_dependency_lock_rejects_external_sources_and_directives(managed):
    installation, _, candidate = managed
    bad_locks = (
        b'--extra-index-url https://evil.example/simple\n',
        b'synthetic-new-dependency @ https://evil.example/pkg.whl --hash=sha256:' + b'a' * 64 + b'\n',
        b'synthetic-new-dependency==1\n',
    )
    for index, lock in enumerate(bad_locks, start=2):
        data, envelope, keys, target = candidate({
            'requirements.txt': f'synthetic-new-dependency=={index}\n'.encode(),
            'requirements-lock.txt': lock,
            'example.py': f'VERSION={index}\n'.encode(),
        })
        installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
        with pytest.raises(ValueError):
            installation.prepare_runtime(target, timeout=60)


def test_unchanged_dependencies_reuse_qualified_previous_runtime(managed):
    installation, previous, candidate = managed
    data, envelope, keys, target = candidate()
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    result = installation.prepare_runtime(target, timeout=60)
    assert result['status'] == 'DEPENDENCIES_UNCHANGED'
    assert result['python'] == installation.runtime(previous)
    assert installation.runtime(target) == installation.runtime(previous)


def test_later_release_reuses_active_dependency_runtime_not_bootstrap(managed, monkeypatch):
    installation, previous, candidate = managed
    requirements = b'synthetic-new-dependency==1\n'
    changes = {'requirements.txt': requirements, 'requirements-lock.txt': LOCK}
    data, envelope, keys, first = candidate(changes)
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    calls = []
    monkeypatch.setattr('update_center.runtime_install.run_process', _fake_runtime_runner(calls))
    prepared = installation.prepare_runtime(first, timeout=60)
    first_python = prepared['python']
    installation._switch(first, expected_current=previous)
    assert installation.runtime(first) == first_python

    data, envelope, keys, second = candidate({
        **changes,
        'example.py': b'VERSION=3\n',
    })
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    reused = installation.prepare_runtime(second, timeout=60)
    assert reused['status'] == 'DEPENDENCIES_UNCHANGED'
    assert installation.runtime(second) == first_python
