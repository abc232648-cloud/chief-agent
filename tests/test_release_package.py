import hashlib
from pathlib import Path
import zipfile

import pytest

from compatibility.package import verify_source_archive


def make_zip(path,files):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in files.items():z.writestr(name,data)
    rel={name.split('/',1)[1]:hashlib.sha256(data).hexdigest() for name,data in files.items()}
    tree=hashlib.sha256('\n'.join(name+'\0'+rel[name] for name in sorted(rel)).encode()).hexdigest()
    return tree


def test_verified_archive_matches_tree_and_counts(tmp_path):
    path=tmp_path/'r.zip';tree=make_zip(path,{'chief-agent/a.py':b'a','chief-agent/.env.example':b'EXAMPLE=1'})
    result=verify_source_archive(path,expected_source_tree_sha256=tree)
    assert result['file_count']==2 and result['private_state_excluded'] is True


@pytest.mark.parametrize('name',[
    'chief-agent/.env','chief-agent/database/worker.db','chief-agent/private.sqlite3',
    'chief-agent/site-sessions/a.json','chief-agent/notifications/outbox/mail.txt',
    'chief-agent/candidate/cv/private.pdf','chief-agent/logs/audit.txt','chief-agent/email-settings.json',
])
def test_private_state_is_rejected_even_if_manifest_claims_exclusion(tmp_path,name):
    path=tmp_path/'r.zip';tree=make_zip(path,{name:b'private'})
    with pytest.raises(ValueError,match='Private/runtime state'):verify_source_archive(path,expected_source_tree_sha256=tree)


def test_wrong_tree_identity_is_rejected(tmp_path):
    path=tmp_path/'r.zip';make_zip(path,{'chief-agent/a.py':b'a'})
    with pytest.raises(ValueError,match='identity mismatch'):verify_source_archive(path,expected_source_tree_sha256='f'*64)


def test_path_traversal_and_wrong_root_are_rejected(tmp_path):
    path=tmp_path/'r.zip'
    with zipfile.ZipFile(path,'w') as z:z.writestr('chief-agent/../escape.txt','x')
    with pytest.raises(ValueError):verify_source_archive(path,expected_source_tree_sha256='f'*64)
    path2=tmp_path/'r2.zip';tree=make_zip(path2,{'other/a.py':b'a'})
    with pytest.raises(ValueError,match='expected single source root'):verify_source_archive(path2,expected_source_tree_sha256=tree)


@pytest.mark.parametrize('name',['identity/passwords.py','tests/test_foundation_passwords.py','tests/test_foundation_secrets.py'])
def test_known_source_modules_are_not_mistaken_for_credentials(tmp_path,name):
    path=tmp_path/'r.zip';tree=make_zip(path,{'chief-agent/'+name:b'# source code, no credentials\n'})
    assert verify_source_archive(path,expected_source_tree_sha256=tree)['file_count']==1


@pytest.mark.parametrize('name',['identity/passwords.json','identity/my_passwords.py','model-credentials/key.cred','diagnostics/dashboard.log'])
def test_source_exception_does_not_admit_private_material(tmp_path,name):
    path=tmp_path/'r.zip';tree=make_zip(path,{'chief-agent/'+name:b'synthetic private material'})
    with pytest.raises(ValueError,match='Private/runtime state'):verify_source_archive(path,expected_source_tree_sha256=tree)


@pytest.mark.parametrize('change',['none','hash','extra'])
def test_development_manifest_must_match_verified_bytes(tmp_path,change):
    import json
    path=tmp_path/'r.zip';tree=make_zip(path,{'chief-agent/a.py':b'a'})
    manifest={'source_tree_sha256':tree,'files':{'a.py':hashlib.sha256(b'a').hexdigest()}}
    if change=='hash':manifest['files']['a.py']='0'*64
    if change=='extra':manifest['credential']='synthetic-private'
    with zipfile.ZipFile(path,'a') as z:z.writestr('SOURCE_MANIFEST.json',json.dumps(manifest))
    if change=='none':assert verify_source_archive(path,expected_source_tree_sha256=tree)['file_count']==1
    else:
        with pytest.raises(ValueError,match='manifest'):verify_source_archive(path,expected_source_tree_sha256=tree)


def test_complete_current_source_archive_is_verified(tmp_path):
    import json
    root=Path(__file__).resolve().parents[1]
    files={str(f.relative_to(root).as_posix()):f.read_bytes() for f in root.rglob('*') if f.is_file() and '__pycache__' not in f.parts and '.pytest_cache' not in f.parts}
    path=tmp_path/'actual-source.zip';tree=make_zip(path,{'chief-agent/'+n:data for n,data in files.items()})
    manifest={'source_tree_sha256':tree,'files':{n:hashlib.sha256(data).hexdigest() for n,data in files.items()}}
    with zipfile.ZipFile(path,'a') as z:z.writestr('SOURCE_MANIFEST.json',json.dumps(manifest))
    assert verify_source_archive(path,expected_source_tree_sha256=tree)['file_count']==len(files)


@pytest.mark.parametrize('name',['chief-agent/a.','chief-agent/CON','chief-agent/a//b.py'])
def test_cross_platform_path_aliases_are_rejected(tmp_path,name):
    path=tmp_path/'unsafe.zip'
    with zipfile.ZipFile(path,'w') as z:z.writestr(name,b'x')
    with pytest.raises(ValueError):verify_source_archive(path,expected_source_tree_sha256='0'*64)


def test_case_aliases_are_rejected_on_both_platforms(tmp_path):
    path=tmp_path/'r.zip';tree=make_zip(path,{'chief-agent/a.py':b'a','chief-agent/A.py':b'b'})
    with pytest.raises(ValueError,match='Duplicate'):verify_source_archive(path,expected_source_tree_sha256=tree)
