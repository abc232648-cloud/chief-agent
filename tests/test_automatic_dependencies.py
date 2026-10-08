import io,json,os,zipfile
from pathlib import Path
import pytest
from installation import automatic as a
from installation.profiles import selected_profiles, source_profile
from tests.test_offline_bootstrap import inputs
from tests.test_installation_preparation import modern


def extended_inputs():
    source,wheels,args=inputs()
    with zipfile.ZipFile(io.BytesIO(source)) as z:
        manifest=json.loads(z.read('SOURCE_MANIFEST.json'));files={n:z.read('source/'+n) for n in manifest['files']}
    family='windows' if os.name=='nt' else 'ubuntu'
    for suffix in ('hashed.txt','inventory.json'):
        files[f'release/{family}-extended-{suffix}']=files[f'release/{family}-{suffix}']
    files['requirements-scrapy.txt']=b'synthetic-chief-test==1.0.0\n'
    files['requirements-test.txt']=b'-r requirements.txt\n-r requirements-scrapy.txt\n'
    source,pins=modern(files);args={**args,**pins};args.pop('expected_wheels')
    return source,wheels,args


def test_selection_is_explicit_core_required_and_test_is_separate():
    assert selected_profiles([])==['core']
    assert selected_profiles(['scrapy'])==['core','scrapy']
    assert selected_profiles(['test'])==['test']
    for bad in (['unknown'],['core','core'],['test','scrapy'],'scrapy'):
        with pytest.raises(ValueError):selected_profiles(bad)


def test_incomplete_test_profile_rejected_before_network_or_writes(tmp_path,monkeypatch):
    source,wheels,args=inputs();args.pop('expected_wheels');before=set(tmp_path.iterdir())
    monkeypatch.setattr(a,'_download',lambda *x:pytest.fail('Network must not start'))
    with pytest.raises(ValueError):a.prepare_selected(source,tmp_path,components=['test'],**args)
    assert set(tmp_path.iterdir())==before


def test_real_profile_locks_include_scrapy_and_match_requirement_closure():
    root=Path(__file__).resolve().parents[1]
    class Tree:
        def read(self,n):return (root/n).read_bytes()
    for family in ('windows','ubuntu'):
        for profile in ('core','scrapy','test'):
            lock,_=source_profile(Tree(),'',family,profile)
            assert ('Scrapy==2.19.0' in lock)==(profile!='core')


def test_download_and_real_isolated_bootstrap_for_selected_components(tmp_path,monkeypatch):
    source,wheels,args=extended_inputs();calls=[]
    monkeypatch.setenv('GROQ_API_KEY','synthetic-secret-must-not-leak')
    def download(lock,destination,env):
        assert 'GROQ_API_KEY' not in env and 'PYTHONPATH' not in env
        calls.append(lock.name)
        with zipfile.ZipFile(io.BytesIO(wheels)) as z:
            for n in z.namelist():(destination/n).write_bytes(z.read(n))
    monkeypatch.setattr(a,'_download',download)
    result=a.prepare_selected(source,tmp_path,components=['scrapy'],**args)
    assert result['status']=='SELECTED_PYTHON_DEPENDENCIES_PREPARED_NOT_ACTIVATED'
    assert calls==['core.txt','scrapy.txt']
    assert set(result['environments'])=={'core','scrapy'}
    assert result['environments']['core']!=result['environments']['scrapy']
    assert all(Path(p).is_file() for p in result['environments'].values())
    assert result['launch_environment']['CHIEF_SCRAPY_PYTHON']==result['environments']['scrapy']
    assert result['activation']=='BLOCKED' and not result['external_actions_enabled']
    assert json.loads((tmp_path/result['download_id']/'result.json').read_text())==result


def test_download_failure_never_reports_success_or_installs_other_components(tmp_path,monkeypatch):
    source,wheels,args=extended_inputs()
    def fail(*args):raise RuntimeError('synthetic-private-token')
    monkeypatch.setattr(a,'_download',fail)
    result=a.prepare_selected(source,tmp_path,components=['scrapy'],**args)
    assert result['status']=='DEPENDENCIES_FAILED'
    assert result['components']['core']['status']=='FAILED'
    assert result['components']['scrapy']['status']=='NOT_ATTEMPTED'
    assert not result['environments'] and 'synthetic-private-token' not in json.dumps(result)


def test_tampered_download_rejected_before_environment_creation(tmp_path,monkeypatch):
    source,wheels,args=extended_inputs()
    def corrupt(lock,destination,env):(destination/'tampered.whl').write_bytes(b'not a wheel')
    monkeypatch.setattr(a,'_download',corrupt)
    monkeypatch.setattr(a,'prepare_environment',lambda *x,**k:pytest.fail('Tampered download reached install'))
    result=a.prepare_selected(source,tmp_path,components=[],**args)
    assert result['status']=='DEPENDENCIES_FAILED' and not result['environments']


def test_download_uses_fixed_index_pins_binary_and_no_resolution(monkeypatch,tmp_path):
    class Result:returncode=0
    def run(cmd,**kw):
        assert '--require-hashes' in cmd and '--no-deps' in cmd and '--only-binary=:all:' in cmd
        assert cmd[cmd.index('--index-url')+1]=='https://pypi.org/simple'
        assert '--isolated' in cmd and kw['timeout']==600
        assert kw['stdout']==a.subprocess.DEVNULL and kw['stderr']==a.subprocess.DEVNULL
        return Result()
    monkeypatch.setattr(a.subprocess,'run',run)
    a._download(tmp_path/'lock',tmp_path/'wheels',{})
