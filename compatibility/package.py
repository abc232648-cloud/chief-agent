"""Verify a source release archive without extracting or trusting its manifest claim."""
import hashlib
import json
from pathlib import PurePosixPath
import stat
import zipfile

MAX_FILES = 10000
MAX_FILE_SIZE = 128 * 1024 * 1024
MAX_TOTAL_SIZE = 512 * 1024 * 1024


def _safe_path(name):
    from operations.backup import _relative
    return _relative(name)


def _private(rel):
    parts=[p.lower() for p in rel.parts]
    name=parts[-1]
    if name.startswith('.env') and name != '.env.example':return True
    if name in {'email-settings.json','cookies.json','auth.json','state.db'}:return True
    # Exact known source modules/tests, not a blanket exemption for .py files.
    source_code={'identity/passwords.py','tests/test_foundation_secrets.py','tests/test_foundation_passwords.py'}
    if any(x in name for x in ('credential','password','secret')) and str(rel) not in source_code:return True
    if rel.suffix.lower() in {'.db','.sqlite','.sqlite3','.dpapi','.cred','.pem','.key','.p12','.pfx','.pyc'}:return True
    if any(p in {'site-sessions','backups','backup','sessions','model-credentials','service-control','diagnostics','__pycache__','.pytest_cache'} for p in parts):return True
    if parts[0] in {'logs'} and name != '.gitkeep':return True
    if len(parts)>1 and parts[0]=='notifications' and parts[1]=='outbox' and name != '.gitkeep':return True
    if parts[0]=='candidate' and name != '.gitkeep' and any(p in {'cv','cv_variants','profile_links','application_history'} for p in parts):return True
    return False


def verify_source_archive(path, *, expected_source_tree_sha256, expected_root='chief-agent'):
    if not isinstance(expected_source_tree_sha256,str) or len(expected_source_tree_sha256)!=64:
        raise ValueError('Expected source tree SHA-256 is required.')
    file_hashes={};total=0;embedded=None;seen_paths=set()
    with zipfile.ZipFile(path) as archive:
        infos=archive.infolist()
        if len(infos)>MAX_FILES:raise ValueError('Release archive contains too many entries.')
        for info in infos:
            if info.flag_bits & 0x1:raise ValueError('Encrypted release entries are not accepted.')
            full=_safe_path(info.filename.rstrip('/')) if info.filename.rstrip('/') else None
            if full is None:continue
            mode=(info.external_attr>>16)&0xFFFF
            if stat.S_ISLNK(mode):raise ValueError('Release archive symlinks are not accepted.')
            if info.is_dir():continue
            canonical=str(full).casefold()
            if canonical in seen_paths:raise ValueError('Duplicate release path alias.')
            seen_paths.add(canonical)
            if info.file_size>MAX_FILE_SIZE:raise ValueError('Release entry exceeds size limit.')
            total+=info.file_size
            if total>MAX_TOTAL_SIZE:raise ValueError('Release archive exceeds size limit.')
            if str(full)=='SOURCE_MANIFEST.json':
                if embedded is not None or info.file_size>8*1024*1024:raise ValueError('Invalid source manifest envelope.')
                embedded=json.loads(archive.read(info))
                if not isinstance(embedded,dict) or not {'files','source_tree_sha256'}<=set(embedded) or set(embedded)-{'files','source_tree_sha256','baseline_source_tree_sha256'}:
                    raise ValueError('Invalid source manifest envelope.')
                baseline=embedded.get('baseline_source_tree_sha256')
                if baseline is not None and (not isinstance(baseline,str) or len(baseline)!=64 or any(c not in '0123456789abcdef' for c in baseline)):
                    raise ValueError('Invalid baseline source identity.')
                continue
            if not full.parts or full.parts[0]!=expected_root or len(full.parts)<2:
                raise ValueError('Release archive must use the expected single source root.')
            rel=PurePosixPath(*full.parts[1:])
            if _private(rel):raise ValueError('Private/runtime state is not allowed in release packages: '+str(rel))
            key=str(rel)
            if key in file_hashes:raise ValueError('Duplicate release path: '+key)
            with archive.open(info) as stream:
                digest=hashlib.file_digest(stream,'sha256').hexdigest()
            file_hashes[key]=digest
    if not file_hashes:raise ValueError('Release archive has no source files.')
    tree=hashlib.sha256('\n'.join(name+'\0'+file_hashes[name] for name in sorted(file_hashes)).encode()).hexdigest()
    if tree!=expected_source_tree_sha256:raise ValueError('Release archive source tree identity mismatch.')
    if embedded is not None and (embedded['files']!=file_hashes or embedded['source_tree_sha256']!=tree):
        raise ValueError('Source manifest does not match verified archive bytes.')
    return {'source_tree_sha256':tree,'file_count':len(file_hashes),'total_uncompressed_bytes':total,'private_state_excluded':True}
