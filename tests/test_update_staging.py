import hashlib
import io
import json
import os
import stat
import zipfile
import pytest
from update_center.staging import inspect_package,stage_package


def package(files=None,extra=None):
    files=files or {'application/example.py':b'print("synthetic")\n'}
    hashes={n:hashlib.sha256(v).hexdigest() for n,v in files.items()}
    tree=hashlib.sha256('\n'.join(n+'\0'+hashes[n] for n in sorted(hashes)).encode()).hexdigest()
    manifest={'source_tree_sha256':tree,'files':hashes}
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('SOURCE_MANIFEST.json',json.dumps(manifest))
        for n,v in files.items():z.writestr('chief-agent/'+n,v)
        if extra:extra(z)
    data=out.getvalue()
    return data,{'expected_archive':hashlib.sha256(data).hexdigest(),'expected_source':tree}


def test_stage_is_inert_exact_and_preserves_existing_files(tmp_path):
    data,pins=package();(tmp_path/'live.txt').write_text('unchanged')
    result=stage_package(data,tmp_path,**pins,private_storage_confirmed=True)
    folder=tmp_path/result['stage_id']
    assert (folder/'source.zip').read_bytes()==data
    assert json.loads((folder/'receipt.json').read_text())==result
    assert set(p.name for p in folder.iterdir())=={'source.zip','receipt.json'}
    assert result['status']=='STAGED_NOT_INSTALLED'
    assert result['compatibility']=='NOT_EVALUATED'
    assert result['activation']=='NOT_IMPLEMENTED'
    assert result['data_migration'] is False
    assert (tmp_path/'live.txt').read_text()=='unchanged'
    if os.name!='nt':assert (folder/'source.zip').stat().st_mode & 0o777==0o600


def test_independent_digest_and_tree_pins_required():
    data,pins=package()
    for key in pins:
        with pytest.raises(ValueError):inspect_package(data,**{**pins,key:'0'*64})


def test_unlisted_entry_rejected():
    data,pins=package(extra=lambda z:z.writestr('chief-agent/extra.py','unexpected'))
    with pytest.raises(ValueError):inspect_package(data,**pins)


@pytest.mark.parametrize('name',['../escape','/absolute','a\\b','C:ads','a//b','CON.txt','a./b','a /b','a?.py','a|b.py'])
def test_platform_unsafe_paths_rejected(name):
    data,pins=package({name:b'x'})
    with pytest.raises(ValueError):inspect_package(data,**pins)


@pytest.mark.parametrize('name',['.env','private/key.txt','config/credentials.json','records.db','db.sqlite3-wal','secrets/a.txt','id_ed25519','site-sessions/session.json','candidate/cv/resume.pdf','logs/audit.txt','notifications/outbox/message.txt','email-settings.json'])
def test_operational_payload_rejected(name):
    data,pins=package({name:b'synthetic-placeholder'})
    with pytest.raises(ValueError):inspect_package(data,**pins)


def test_zip_symlink_rejected_without_host_privilege():
    def extra(z):
        i=zipfile.ZipInfo('chief-agent/link');i.create_system=3
        i.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(i,'outside')
    data,pins=package(extra=extra)
    with pytest.raises(ValueError):inspect_package(data,**pins)


def test_case_collision_rejected():
    data,pins=package({'a.py':b'a','A.py':b'b'})
    with pytest.raises(ValueError):inspect_package(data,**pins)


def test_file_digest_mismatch_rejected():
    data,pins=package();out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as old,zipfile.ZipFile(out,'w') as new:
        for name in old.namelist():new.writestr(name,b'changed' if name.endswith('.py') else old.read(name))
    data=out.getvalue();pins['expected_archive']=hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError):inspect_package(data,**pins)


def test_bounded_expansion(monkeypatch):
    import update_center.staging as s
    data,pins=package();monkeypatch.setattr(s,'MAX_EXPANDED',1)
    with pytest.raises(ValueError):inspect_package(data,**pins)


def test_storage_confirmation_required(tmp_path):
    data,pins=package()
    with pytest.raises(ValueError):stage_package(data,tmp_path,**pins)
    assert not list(tmp_path.glob('*/source.zip'))


def test_interrupted_stage_cleanup_preserves_sibling(tmp_path,monkeypatch):
    import update_center.staging as s
    (tmp_path/'existing').mkdir();(tmp_path/'existing/receipt.json').write_text('preserve')
    original=s._write
    def fail(payloads,created,open_file,directory_fd):
        original({'source.zip':payloads['source.zip']},created,open_file,directory_fd)
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(s,'_write',fail);data,pins=package()
    before=set(tmp_path.iterdir())
    with pytest.raises(OSError):stage_package(data,tmp_path,**pins,private_storage_confirmed=True)
    assert set(tmp_path.iterdir())==before
    assert (tmp_path/'existing/receipt.json').read_text()=='preserve'


def test_incomplete_slot_never_reused(tmp_path):
    (tmp_path/'incomplete').mkdir();(tmp_path/'incomplete/source.zip').write_bytes(b'incomplete')
    data,pins=package()
    first=stage_package(data,tmp_path,**pins,private_storage_confirmed=True)
    second=stage_package(data,tmp_path,**pins,private_storage_confirmed=True)
    assert first['stage_id']!=second['stage_id']
    assert (tmp_path/'incomplete/source.zip').read_bytes()==b'incomplete'


@pytest.mark.skipif(os.name=='nt',reason='Native symlink fixture exercised on Ubuntu; archive-link rejection runs on both OSs.')
def test_staging_root_symlink_rejected(tmp_path):
    outside=tmp_path/'outside';outside.mkdir();link=tmp_path/'link';link.symlink_to(outside,target_is_directory=True)
    data,pins=package()
    with pytest.raises(OSError):stage_package(data,link,**pins,private_storage_confirmed=True)
    assert not list(outside.iterdir())


@pytest.mark.skipif(os.name!='nt',reason='Windows junction test; Ubuntu exercises native symlink containment.')
def test_windows_staging_root_junction_rejected(tmp_path):
    import subprocess
    outside=tmp_path/'outside';outside.mkdir();link=tmp_path/'link'
    subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],check=True,capture_output=True)
    try:
        data,pins=package()
        with pytest.raises(ValueError):stage_package(data,link,**pins,private_storage_confirmed=True)
        assert not list(outside.iterdir())
    finally:os.rmdir(link)


def test_cli_failure_is_sanitized_and_does_not_create_stage(tmp_path,capsys):
    from application.update_stage import main
    secret='synthetic-sensitive-name'
    assert main([str(tmp_path/secret),'--archive-sha256','0'*64,'--source-sha256','0'*64,
                 '--staging-root',str(tmp_path),'--confirm-private-storage'])==2
    output=capsys.readouterr().out
    assert secret not in output
    assert json.loads(output)['status']=='STAGING_FAILED'


def test_cli_stages_without_extraction(tmp_path,capsys):
    from application.update_stage import main
    data,pins=package();archive=tmp_path/'input.zip';archive.write_bytes(data)
    assert main([str(archive),'--archive-sha256',pins['expected_archive'],
                 '--source-sha256',pins['expected_source'],'--staging-root',str(tmp_path),
                 '--confirm-private-storage'])==0
    result=json.loads(capsys.readouterr().out)
    assert (tmp_path/result['stage_id']/'source.zip').read_bytes()==data
    assert not (tmp_path/'application').exists()
