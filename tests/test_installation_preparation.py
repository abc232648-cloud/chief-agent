import hashlib
import io
import json
import os
from pathlib import Path
import zipfile
import pytest

from installation.preparation import prepare_source
from update_center.staging import inspect_package
from tests.test_update_staging import package


def modern(files=None):
    files=files or {'application/main.py':b'raise RuntimeError("MUST NOT EXECUTE")\n','requirements.txt':b'synthetic==1\n'}
    hashes={n:hashlib.sha256(data).hexdigest() for n,data in files.items()}
    identity=hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    manifest={'source_tree_sha256':identity,'files':hashes,'hash_method':'compact sorted JSON files-map SHA256'}
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('SOURCE_MANIFEST.json',json.dumps(manifest))
        for name,data in files.items():z.writestr('source/'+name,data)
    data=out.getvalue()
    return data,{'expected_archive':hashlib.sha256(data).hexdigest(),'expected_source':identity}


@pytest.mark.parametrize('factory',[package,modern])
def test_prepare_both_explicit_identity_formats_inertly(tmp_path,factory):
    data,pins=factory();live=tmp_path/'live';live.mkdir();(live/'database.db').write_bytes(b'preserve')
    receipt=prepare_source(data,tmp_path,**pins,private_storage_confirmed=True)
    slot=tmp_path/receipt['preparation_id'];manifest=json.loads((slot/'SOURCE_MANIFEST.json').read_text())
    assert json.loads((slot/'preparation.json').read_text())==receipt
    assert receipt['activation']=='BLOCKED' and receipt['environment']=='NOT_PREPARED'
    assert receipt['status']=='SOURCE_PREPARED_NOT_INSTALLED' and receipt['data_migration'] is False
    assert (live/'database.db').read_bytes()==b'preserve'
    for name,digest in manifest['files'].items():
        assert hashlib.sha256((slot/'source'/name).read_bytes()).hexdigest()==digest
        if os.name!='nt':assert (slot/'source'/name).stat().st_mode & 0o777==0o600


@pytest.mark.parametrize('files',[
    {'a':b'1','a/b':b'2'}, {'A/x.py':b'1','a/y.py':b'2'},
    {'private/secrets.txt':b'no'}, {'../outside':b'no'}, {'x.db':b'no'},
])
def test_preparation_rejects_aliases_and_private_or_escaping_payloads(tmp_path,files):
    before=set(tmp_path.iterdir())
    data,pins=modern(files)
    with pytest.raises(ValueError):prepare_source(data,tmp_path,**pins,private_storage_confirmed=True)
    assert set(tmp_path.iterdir())==before


def test_no_receipt_on_mid_write_failure_and_existing_sibling_preserved(tmp_path,monkeypatch):
    import operations.private_tree as module
    (tmp_path/'sibling').write_bytes(b'preserve');before=set(tmp_path.iterdir());original=module.os.fsync;calls=0
    def fail(fd):
        nonlocal calls
        calls+=1
        if calls==3:raise OSError('synthetic failure')
        return original(fd)
    monkeypatch.setattr(module.os,'fsync',fail)
    data,pins=modern()
    with pytest.raises(OSError):prepare_source(data,tmp_path,**pins,private_storage_confirmed=True)
    assert set(tmp_path.iterdir())==before
    assert (tmp_path/'sibling').read_bytes()==b'preserve'


def test_retry_uses_new_slot_and_never_reuses_interrupted_slot(tmp_path):
    incomplete=tmp_path/'prepare-incomplete';incomplete.mkdir();(incomplete/'partial').write_bytes(b'keep')
    data,pins=modern();a=prepare_source(data,tmp_path,**pins,private_storage_confirmed=True)
    b=prepare_source(data,tmp_path,**pins,private_storage_confirmed=True)
    assert a['preparation_id']!=b['preparation_id']
    assert (incomplete/'partial').read_bytes()==b'keep'


def test_privacy_and_digest_confirmation_required(tmp_path):
    before=set(tmp_path.iterdir())
    data,pins=modern()
    with pytest.raises(ValueError):prepare_source(data,tmp_path,**pins)
    with pytest.raises(ValueError):prepare_source(data,tmp_path,**{**pins,'expected_source':'0'*64},private_storage_confirmed=True)
    assert set(tmp_path.iterdir())==before


def test_cli_sanitizes_bad_zip_and_paths(tmp_path,capsys):
    from application.prepare_installation import main
    archive=tmp_path/'private-name.zip';data=b'not zip';archive.write_bytes(data)
    assert main([str(archive),'--archive-sha256',hashlib.sha256(data).hexdigest(),
        '--source-sha256','0'*64,'--installation-root',str(tmp_path),'--confirm-private-storage'])==2
    output=capsys.readouterr().out
    assert 'private-name' not in output and json.loads(output)['status']=='PREPARATION_FAILED'


@pytest.mark.skipif(os.name=='nt',reason='POSIX permissions and native symlink fixture; Windows junction test runs separately.')
def test_linux_shared_or_symlink_root_rejected(tmp_path):
    data,pins=modern();shared=tmp_path/'shared';shared.mkdir(mode=0o755);shared.chmod(0o755)
    with pytest.raises(ValueError):prepare_source(data,shared,**pins,private_storage_confirmed=True)
    outside=tmp_path/'outside';outside.mkdir(mode=0o700);link=tmp_path/'link';link.symlink_to(outside,target_is_directory=True)
    with pytest.raises(OSError):prepare_source(data,link,**pins,private_storage_confirmed=True)
    assert not list(outside.iterdir())


@pytest.mark.skipif(os.name!='nt',reason='Native Windows junction test; Linux symlink test runs separately.')
def test_windows_junction_root_rejected(tmp_path):
    import subprocess
    data,pins=modern();outside=tmp_path/'outside';outside.mkdir();link=tmp_path/'link'
    subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],check=True,capture_output=True)
    try:
        with pytest.raises(ValueError):prepare_source(data,link,**pins,private_storage_confirmed=True)
        assert not list(outside.iterdir())
    finally:os.rmdir(link)


def test_wrong_hash_method_rejected(tmp_path):
    data,pins=modern();out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as old,zipfile.ZipFile(out,'w') as new:
        for name in old.namelist():
            value=old.read(name)
            if name=='SOURCE_MANIFEST.json':
                value=json.loads(value);value['hash_method']='guess';value=json.dumps(value).encode()
            new.writestr(name,value)
    data=out.getvalue();pins['expected_archive']=hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError,match='identity method'):inspect_package(data,**pins)
