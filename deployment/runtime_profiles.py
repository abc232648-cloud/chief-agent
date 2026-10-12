"""Chief-owned external runtime profiles; never reuse desktop/global state implicitly."""
import json
import os
import re
from pathlib import Path

from operations.backup import _plain

_MARKER='.chief-runtime-profile.json'
_ID=re.compile(r'^[a-z0-9][a-z0-9._-]{0,127}$')


class RuntimeProfileConflict(PermissionError):
    pass


def _candidate_id(value):
    if not isinstance(value,str) or not _ID.fullmatch(value):
        raise ValueError('Invalid runtime candidate identity.')
    return value


def _marker_object(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('Duplicate runtime profile marker field.')
        value[key]=item
    return value


def profile_path(state_root,candidate_id):
    root=_plain(Path(state_root))
    candidate_id=_candidate_id(candidate_id)
    return root/'runtime-profiles'/candidate_id


def inspect_profile(state_root,candidate_id):
    """Describe one reserved Chief profile without creating or modifying it."""
    candidate_id=_candidate_id(candidate_id)
    path=profile_path(state_root,candidate_id)
    if not path.exists() and not path.is_symlink():
        return {'candidate_id':candidate_id,'status':'CREATE_REQUIRED','path':str(path)}
    try:_plain(path)
    except Exception as exc:
        raise RuntimeProfileConflict('Runtime profile path is linked or otherwise unsafe.') from exc
    if not path.is_dir():
        raise RuntimeProfileConflict('Reserved runtime profile path already exists and is not a directory.')
    marker=path/_MARKER
    if not marker.exists() and not marker.is_symlink():
        raise RuntimeProfileConflict('Existing runtime profile is not Chief-owned; select or create an isolated profile instead.')
    try:
        _plain(marker,file=True)
        if marker.stat().st_size>4096:raise ValueError('marker too large')
        data=json.loads(marker.read_text(encoding='utf-8'),object_pairs_hook=_marker_object)
    except Exception as exc:
        raise RuntimeProfileConflict('Existing runtime profile ownership marker is invalid.') from exc
    expected={'format_version':1,'owner':'CHIEF','candidate_id':candidate_id}
    if data!=expected:
        raise RuntimeProfileConflict('Existing runtime profile belongs to another runtime identity or owner.')
    return {'candidate_id':candidate_id,'status':'CHIEF_OWNED','path':str(path)}


def prepare_profile(state_root,candidate_id):
    """Create only the reserved Chief profile; never import or alter a global profile."""
    plan=inspect_profile(state_root,candidate_id)
    if plan['status']=='CHIEF_OWNED':return plan
    root=_plain(Path(state_root));parent=root/'runtime-profiles'
    parent.mkdir(mode=0o700,exist_ok=True)
    _plain(parent)
    try:
        Path(plan['path']).mkdir(mode=0o700)
        marker=Path(plan['path'])/_MARKER
        fd=os.open(marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump({'format_version':1,'owner':'CHIEF','candidate_id':candidate_id},stream,sort_keys=True)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
    except BaseException:
        # Never adopt or recursively clean a path after a concurrent/partial write.
        # An incomplete nonempty profile intentionally remains a conflict for review.
        path=Path(plan['path'])
        if path.exists() and path.is_dir() and not any(path.iterdir()):path.rmdir()
        raise
    return inspect_profile(root,candidate_id)


def plans_from_source(state_root,source_root):
    """Reserve only role-assigned candidates from pinned policy; never discover host profiles."""
    source_root=Path(source_root)
    from runtime_qualification.catalog import load_candidates
    from runtime_qualification.policy import load_policy
    candidates=load_candidates((source_root/'config/runtime_candidates.json').read_text(encoding='utf-8'))
    policy=load_policy((source_root/'config/runtime_policy.json').read_text(encoding='utf-8'),candidates)
    ids=(policy.execution_candidate_id,policy.research_candidate_id)
    return tuple(inspect_profile(state_root,candidate_id) for candidate_id in ids)
