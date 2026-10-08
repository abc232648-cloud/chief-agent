import hashlib
import io
import json
import os
from pathlib import Path
import zipfile
import pytest
from installation import bootstrap as b
from installation.dependencies import inspect_wheels, locked_requirements
from installation import dependencies as dependency_limits
from tests.test_installation_preparation import modern


def fixture_package(extra=None):
    out=io.BytesIO();name='synthetic_chief_test-1.0.0-py3-none-any.whl'
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('synthetic_chief_test/__init__.py','VALUE = 1\n')
        z.writestr('synthetic_chief_test-1.0.0.dist-info/METADATA','Metadata-Version: 2.1\nName: synthetic-chief-test\nVersion: 1.0.0\n')
        z.writestr('synthetic_chief_test-1.0.0.dist-info/WHEEL','Wheel-Version: 1.0\nGenerator: synthetic-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        z.writestr('synthetic_chief_test-1.0.0.dist-info/RECORD','')
        if extra:
            for path,value in extra.items():z.writestr(path,value)
    raw=out.getvalue();bundle=io.BytesIO()
    with zipfile.ZipFile(bundle,'w') as z:z.writestr(name,raw)
    data=bundle.getvalue();lock='synthetic-chief-test==1.0.0 --hash=sha256:'+hashlib.sha256(raw).hexdigest()+'\n'
    return data,hashlib.sha256(data).hexdigest(),lock


def inputs():
    wheels,wheel_hash,lock=fixture_package();runtime=b.runtime_identity()
    family='windows' if os.name=='nt' else 'ubuntu'
    inventory={k:v for k,v in runtime.items() if k!='executable_sha256'}
    inventory['packages']={'synthetic_chief_test':'1.0.0','pip':runtime['bootstrap_pip_version']}
    source,pins=modern({'application/main.py':b'raise RuntimeError("application must not run")',
        'requirements.txt':b'synthetic-chief-test==1.0.0\n',
        'release/'+family+'-hashed.txt':lock.encode(),
        'release/'+family+'-inventory.json':json.dumps(inventory).encode()})
    return source,wheels,{**pins,'expected_wheels':wheel_hash,'expected_runtime':runtime,
        'private_storage_confirmed':True,'runtime_and_dependencies_reviewed':True}


@pytest.mark.parametrize('line',[
    'thing>=1', 'thing==1 --index-url=https://invalid', 'thing @ https://invalid',
    '-r another-file', 'thing==1 --hash=sha256:bad', 'thing==1',
])
def test_unpinned_or_executable_dependency_options_rejected(line):
    with pytest.raises(ValueError):locked_requirements(line)


@pytest.mark.parametrize('path',['../../escape.py','hook.pth','sitecustomize.py','pkg/usercustomize.py'])
def test_wheel_traversal_and_startup_hooks_rejected(path):
    data,digest,lock=fixture_package({path:b'not executed'})
    with pytest.raises(ValueError):inspect_wheels(data,expected_archive=digest,lock_text=lock)


def test_dependency_hash_is_not_inferred_from_embedded_metadata():
    data,digest,lock=fixture_package()
    with pytest.raises(ValueError):inspect_wheels(data,expected_archive='0'*64,lock_text=lock)
    with pytest.raises(ValueError):inspect_wheels(data,expected_archive=digest,lock_text=lock.replace('1.0.0','2.0.0'))


def test_archive_member_and_total_expansion_limits_are_independent(monkeypatch):
    data,_,_=fixture_package({'synthetic_chief_test/payload.bin':b'x'*20000})
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        name=bundle.namelist()[0]
        with zipfile.ZipFile(io.BytesIO(bundle.read(name))) as original:
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as wheel:
                for item in original.infolist():wheel.writestr(item.filename,original.read(item))
    raw=out.getvalue();outer=io.BytesIO()
    with zipfile.ZipFile(outer,'w') as bundle:bundle.writestr(name,raw)
    data=outer.getvalue();digest=hashlib.sha256(data).hexdigest()
    lock='synthetic-chief-test==1.0.0 --hash=sha256:'+hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(dependency_limits,'MAX_WHEEL',10000)
    monkeypatch.setattr(dependency_limits,'MAX_WHEEL_MEMBER',30000)
    monkeypatch.setattr(dependency_limits,'MAX_WHEEL_EXPANDED',40000)
    assert inspect_wheels(data,expected_archive=digest,lock_text=lock)
    monkeypatch.setattr(dependency_limits,'MAX_WHEEL_MEMBER',15000)
    with pytest.raises(ValueError,match='expansion'):inspect_wheels(data,expected_archive=digest,lock_text=lock)
    monkeypatch.setattr(dependency_limits,'MAX_WHEEL_MEMBER',30000)
    monkeypatch.setattr(dependency_limits,'MAX_WHEEL_EXPANDED',15000)
    with pytest.raises(ValueError,match='expansion'):inspect_wheels(data,expected_archive=digest,lock_text=lock)


def test_preflight_needs_review_and_exact_runtime_without_writes(tmp_path):
    source,wheels,args=inputs();before=set(tmp_path.iterdir())
    for change in ({'runtime_and_dependencies_reviewed':False},{'expected_runtime':{}}):
        with pytest.raises(ValueError):b.prepare_environment(source,wheels,tmp_path,**{**args,**change})
    assert set(tmp_path.iterdir())==before


def test_failed_environment_stays_inert_and_retry_never_reuses_it(tmp_path,monkeypatch):
    source,wheels,args=inputs();calls=[]
    def fail(command,env,cwd):
        calls.append(command);raise OSError('synthetic secret text must not be recorded')
    monkeypatch.setattr(b,'_run',fail)
    first=b.prepare_environment(source,wheels,tmp_path,**args)
    second=b.prepare_environment(source,wheels,tmp_path,**args)
    assert first['status']=='ENVIRONMENT_PREPARATION_FAILED' and first['activation']=='BLOCKED'
    assert first['preparation_id']!=second['preparation_id'] and len(calls)==2
    assert 'synthetic secret' not in json.dumps(first)
    assert (tmp_path/first['preparation_id']/'attempt.json').exists()


def test_bootstrap_does_not_pass_secrets_or_network_resolution(monkeypatch,tmp_path):
    source,wheels,args=inputs();commands=[]
    monkeypatch.setenv('GROQ_API_KEY','synthetic-never-pass')
    monkeypatch.setenv('PYTHONPATH','synthetic-never-pass')
    def record(command,env,cwd):
        assert 'GROQ_API_KEY' not in env and 'PYTHONPATH' not in env
        assert 'synthetic-never-pass' not in json.dumps(env)
        commands.append(command)
        if 'venv' in command:
            root=Path(command[-1]);python=root/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
            python.parent.mkdir(parents=True);python.write_bytes(b'synthetic')
            (root/'pyvenv.cfg').write_text('include-system-site-packages = false\n')
    monkeypatch.setattr(b,'_run',record)
    result=b.prepare_environment(source,wheels,tmp_path,**args)
    assert result['activation']=='BLOCKED' and result['data_migration'] is False
    assert len(commands)==4
    assert '--no-index' in commands[1] and '--no-deps' in commands[1]
    assert all('application/main.py' not in ' '.join(command) for command in commands)


def test_real_offline_environment_bootstrap_as_normal_user(tmp_path):
    source,wheels,args=inputs()
    result=b.prepare_environment(source,wheels,tmp_path,**args)
    assert result['status']=='ENVIRONMENT_PREPARED_NOT_ACTIVATED'
    folder=tmp_path/result['preparation_id']
    assert (folder/'environment'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')).is_file()
    assert json.loads((folder/'result.json').read_text())==result
    assert result['compatibility']=='NOT_EVALUATED' and result['publisher_authentication']=='NOT_ESTABLISHED'


def test_interrupted_environment_is_preserved_without_activation(tmp_path,monkeypatch):
    source,wheels,args=inputs()
    def interrupt(*args):raise KeyboardInterrupt()
    monkeypatch.setattr(b,'_run',interrupt)
    result=b.prepare_environment(source,wheels,tmp_path,**args)
    assert result['status']=='ENVIRONMENT_PREPARATION_INTERRUPTED' and result['activation']=='BLOCKED'
    assert (tmp_path/result['preparation_id']/'attempt.json').exists()


def test_cli_missing_input_does_not_expose_private_path(tmp_path,capsys):
    from application.bootstrap_installation import main
    args=[]
    for name in ('source-archive','wheel-archive','runtime-profile','installation-root'):
        args.extend(['--'+name,str(tmp_path/'synthetic-private-name')])
    for name in ('archive-sha256','source-sha256','wheels-sha256'):args.extend(['--'+name,'0'*64])
    assert main(args)==2
    output=capsys.readouterr().out
    assert 'synthetic-private-name' not in output and json.loads(output)['activation']=='BLOCKED'
