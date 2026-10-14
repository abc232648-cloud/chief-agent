import os
from pathlib import Path
import shutil
import subprocess
import sys

from tests.test_managed_updates import managed
from tests.test_update_release_trust import NOW


def test_dependency_change_prepares_release_local_runtime(managed, monkeypatch):
    installation, previous, candidate = managed
    data, envelope, keys, target = candidate({'requirements.txt': b'synthetic-new-dependency==1\n'})
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)

    calls = []
    def fake_run(command, *, cwd, env, output, timeout, max_output_bytes=8*1024*1024):
        calls.append(tuple(command))
        if '-m' in command and 'venv' in command:
            runtime = Path(command[-1])
            python = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            python.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sys.executable, python)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr('update_center.runtime_install.run_process', fake_run)
    result = installation.prepare_runtime(target, timeout=60)
    assert result['status'] == 'RUNTIME_PREPARED'
    assert Path(result['python']).is_relative_to(installation.root / 'releases' / target / 'runtime')
    assert installation.runtime(target) == result['python']
    assert installation.selection() == previous
    assert any('venv' in command for command in calls)
    assert any('pip' in command and 'install' in command for command in calls)
    assert any('pip' in command and 'check' in command for command in calls)


def test_unchanged_dependencies_reuse_qualified_previous_runtime(managed):
    installation, previous, candidate = managed
    data, envelope, keys, target = candidate()
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    result = installation.prepare_runtime(target, timeout=60)
    assert result['status'] == 'DEPENDENCIES_UNCHANGED'
    assert result['python'] == installation.runtime(previous)
    assert installation.runtime(target) == installation.runtime(previous)
