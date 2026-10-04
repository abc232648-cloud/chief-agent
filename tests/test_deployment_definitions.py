import plistlib
import xml.etree.ElementTree as ET
import pytest
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
        assert b'WorkingDirectory=/home/fixture/Chief Agent\n' in data
        assert b'--config' in data and b'ExecStop=' in data and b'NoNewPrivileges=yes' in data
    with pytest.raises(ValueError):definitions('linux','relative','fixture',instance_config='/tmp/instance.json')
    with pytest.raises(ValueError):definitions('linux','/home/fixture\nInjected=yes','fixture',instance_config='/tmp/instance.json')
