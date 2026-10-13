import os
import subprocess
import sys
import time

import pytest

from update_center.processes import run


@pytest.mark.parametrize('parent_wait', [False, True])
def test_normal_completion_and_timeout_remove_child_processes(tmp_path, parent_wait):
    marker = tmp_path / 'escaped.txt'
    ready = tmp_path / 'ready.txt'
    child = "import pathlib,time,sys;pathlib.Path(sys.argv[2]).write_text('ready');time.sleep(2);pathlib.Path(sys.argv[1]).write_text('escaped');time.sleep(30)"
    parent = "import subprocess,sys,time,pathlib;subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]]);r=pathlib.Path(sys.argv[3]);\nwhile not r.exists():time.sleep(.01)\n" + ('time.sleep(30)' if parent_wait else '')
    command = [sys.executable, '-c', parent, child, str(marker), str(ready)]
    with (tmp_path / 'output.log').open('wb') as output:
        if parent_wait:
            with pytest.raises(subprocess.TimeoutExpired):
                run(command, cwd=tmp_path, env=dict(os.environ), output=output, timeout=1)
        else:
            assert run(command, cwd=tmp_path, env=dict(os.environ), output=output, timeout=5).returncode == 0
    assert ready.exists(), 'Child must actually start before lifecycle cleanup is tested.'
    time.sleep(2.2)
    assert not marker.exists(), 'A child survived its test process group.'


def test_process_exit_status_and_output_are_preserved(tmp_path):
    log = tmp_path / 'output.log'
    with log.open('wb') as output:
        result = run([sys.executable, '-c', "print('synthetic-result');raise SystemExit(7)"],
                     cwd=tmp_path, env=dict(os.environ), output=output, timeout=5)
    assert result.returncode == 7
    assert b'synthetic-result' in log.read_bytes()


def test_excessive_output_is_rejected(tmp_path):
    with (tmp_path / 'output.log').open('wb') as output:
        with pytest.raises(ValueError, match='output exceeded'):
            run([sys.executable, '-c', "print('x'*4096)"], cwd=tmp_path,
                env=dict(os.environ), output=output, timeout=5, max_output_bytes=1024)
