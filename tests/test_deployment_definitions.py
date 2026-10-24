import plistlib
import xml.etree.ElementTree as ET
import pytest
import os
from deployment.service_files import definitions


def test_windows_boot_definitions_have_no_stored_password():
    files=definitions('windows',r'C:\Users\Fixture\Chief Agent','Fixture',instance_config=r'C:\Users\Fixture\Private\instance.json')
    assert len(files)==3
    for name,data in files.items():
        assert data.startswith(b'\xff\xfe') or data.startswith(b'\xfe\xff')
        root=ET.fromstring(data);ns={'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}
        assert root.find('.//t:LogonTrigger',ns) is not None
        assert root.find('.//t:LogonType',ns).text=='InteractiveToken'
        assert root.find('.//t:RunLevel',ns).text=='LeastPrivilege'
        assert root.find('.//t:WorkingDirectory',ns).text==r'C:\Users\Fixture\Chief Agent'
        assert root.find('.//t:Password',ns) is None


def test_macos_boot_definitions_run_as_owner():
    for data in definitions('macos','/Users/fixture/Chief Agent','fixture',instance_config='/Users/fixture/private/instance.json').values():
        value=plistlib.loads(data)
        assert value['UserName']=='fixture' and value['RunAtLoad']
        assert value['ProgramArguments'][0]=='/Users/fixture/Chief Agent/.venv/bin/python'
        assert value['Umask']==0o077


def test_linux_units_and_invalid_paths():
    for data in definitions('linux','/home/fixture/Chief Agent','fixture',instance_config='/home/fixture/private/instance.json').values():
        assert b'Restart=on-failure' in data and b'UMask=0077' in data
        assert b'ConditionUser=fixture\n' in data
        assert b'WorkingDirectory=/home/fixture/Chief Agent\n' in data
        assert b'--config' in data and b'ExecStop=' in data and b'NoNewPrivileges=yes' in data
    with pytest.raises(ValueError):definitions('linux','relative','fixture',instance_config='/tmp/instance.json')
    with pytest.raises(ValueError):definitions('linux','/home/fixture\nInjected=yes','fixture',instance_config='/tmp/instance.json')
    with pytest.raises(ValueError,match='local account'):
        definitions('linux','/home/fixture/Chief Agent','fixture service',instance_config='/tmp/instance.json')


def test_managed_linux_definitions_use_stable_launcher_and_same_stop_route():
    files=definitions('linux','/home/fixture/bootstrap','fixture',instance_config='/home/fixture/private/instance.json',managed_installation='/home/fixture/managed')
    for data in files.values():
        assert b'deployment.managed_launch' in data and b'--installation' in data
        assert b'WorkingDirectory=/home/fixture/bootstrap\n' in data
        assert b'"-B" "-E" "-s"' in data and b'--stop' in data
        assert b'--config' not in data


def test_managed_windows_definitions_keep_least_privilege():
    files=definitions('windows',r'C:\Chief\bootstrap','Fixture',instance_config=r'C:\Chief\private\instance.json',managed_installation=r'C:\Chief\managed')
    for data in files.values():
        root=ET.fromstring(data);ns={'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}
        assert root.find('.//t:RunLevel',ns).text=='LeastPrivilege'
        assert root.find('.//t:Command',ns).text.endswith('pythonw.exe')
        assert root.find('.//t:Hidden',ns).text=='true'
        assert 'deployment.managed_launch' in root.find('.//t:Arguments',ns).text
        assert root.find('.//t:WorkingDirectory',ns).text==r'C:\Chief\bootstrap'


@pytest.mark.parametrize('target', ['relative', '/home/fixture/bad\nExecStart=evil', '/home/fixture/%bad', '/home/fixture/$bad'])
def test_managed_definition_rejects_unsafe_paths(target):
    with pytest.raises(ValueError):
        definitions('linux','/home/fixture/bootstrap','fixture',instance_config='/home/fixture/instance.json',managed_installation=target)


def test_managed_launcher_cannot_live_inside_switched_slots():
    with pytest.raises(ValueError,match='Stable launcher'):
        definitions('linux','/home/fixture/managed/releases/source','fixture',instance_config='/home/fixture/instance.json',managed_installation='/home/fixture/managed')


@pytest.mark.parametrize('windows', [True, False])
def test_managed_launch_preserves_service_lifetime(monkeypatch, tmp_path, windows):
    import deployment.managed_launch as ml
    from unittest.mock import MagicMock
    fake_inst = MagicMock()
    fake_inst.selection.return_value = "deadbeef" * 8
    fake_inst.source.return_value = (str(tmp_path), None)
    python_exe = tmp_path / "Scripts" / "python.exe"
    pythonw_exe = tmp_path / "Scripts" / "pythonw.exe"
    python_exe.parent.mkdir(parents=True)
    python_exe.write_text("")
    pythonw_exe.write_text("")
    fake_inst.runtime.return_value = str(python_exe)
    fake_inst.config = {"instance_config": str(tmp_path / "instance.json")}
    fake_inst.root = tmp_path

    from types import SimpleNamespace
    exec_args = []
    run_args = []
    monkeypatch.setattr("update_center.managed.ManagedInstallation", lambda root: fake_inst)
    monkeypatch.setattr(ml, 'os', SimpleNamespace(name='nt' if windows else 'posix',
        environ={}, chdir=lambda d: None,
        execve=lambda py, cmd, env: exec_args.append((py, cmd))))
    def run(cmd, **kwargs):
        run_args.append((cmd, kwargs))
        return SimpleNamespace(returncode=23)
    monkeypatch.setattr(ml, 'subprocess', SimpleNamespace(run=run,
        STARTUPINFO=lambda: SimpleNamespace(dwFlags=0), STARTF_USESHOWWINDOW=1,
        SW_HIDE=0, CREATE_NO_WINDOW=0x08000000, DEVNULL=-3))

    result = ml.main(["--installation", str(tmp_path), "--component", "dashboard"])
    if windows:
        assert result == 23
        assert exec_args == []
        cmd, options = run_args[0]
        assert cmd[0] == str(python_exe)  # verified runtime, not an unverified sibling
        assert options['creationflags'] == 0x08000000
        assert options['startupinfo'].wShowWindow == 0
        assert options['stdin'] == options['stdout'] == options['stderr'] == -3
    else:
        assert run_args == []
        assert exec_args[0][0] == str(python_exe)


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows windowless managed launcher')
def test_native_windowless_managed_handoff_preserves_exit_and_has_no_console(tmp_path):
    import json
    from pathlib import Path
    import subprocess
    import sys
    source = tmp_path / 'slot'
    package = source / 'deployment'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    marker = tmp_path / 'child.json'
    (package / 'launch.py').write_text(
        'import ctypes,json,sys\nfrom pathlib import Path\n'
        f'Path({str(marker)!r}).write_text(json.dumps({{"console":bool(ctypes.windll.kernel32.GetConsoleWindow())}}))\n'
        'sys.exit(17)\n')
    root = str(Path(__file__).resolve().parents[1])
    parent = tmp_path / 'parent.py'
    parent.write_text(
        'import sys,types\nfrom pathlib import Path\n'
        f'sys.path.insert(0,{root!r})\n'
        'from deployment import managed_launch\n'
        'from unittest.mock import MagicMock\n'
        'instance=MagicMock()\n'
        f'instance.root=Path({str(tmp_path)!r})\n'
        'instance.selection.return_value="synthetic-slot"\n'
        f'instance.source.return_value=(Path({str(source)!r}),None)\n'
        f'instance.runtime.return_value={sys.executable!r}\n'
        'instance.config={"instance_config":"unused-fixture"}\n'
        'import update_center.managed\n'
        'update_center.managed.ManagedInstallation=lambda root:instance\n'
        f'raise SystemExit(managed_launch.main(["--installation",{str(tmp_path)!r},"--component","dashboard"]))\n')
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    result = subprocess.run([str(pythonw), str(parent)], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
    assert result.returncode == 17
    assert json.loads(marker.read_text()) == {'console': False}
