"""Bounded, pinned source packages staged inertly; never extract or activate.

The operator must supply an independently reviewed archive digest. Matching an
embedded manifest alone is not publisher authentication. The staging directory
must already be private and operator controlled, as for operational backups.
"""
from contextlib import ExitStack
import hashlib
import io
import json
import os
import re
from pathlib import Path
import stat
import uuid
import zipfile

from compatibility.manifest import _unique_object
from compatibility.package import _private
from operations.backup import _relative, _sha
from operations.draft_files import _windows_directory
from operations.time_integrity import utc_now, utc_text

MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_ENTRIES = 10000


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def inspect_package(data, *, expected_archive, expected_source):
    """Validate the complete flat source ZIP against independent caller pins."""
    _sha(expected_archive); _sha(expected_source)
    if not isinstance(data, bytes) or len(data) > MAX_ARCHIVE or _digest(data) != expected_archive:
        raise ValueError('Package size or archive identity differs.')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries=archive.infolist()
        if not entries or len(entries)>MAX_ENTRIES or sum(e.file_size for e in entries)>MAX_EXPANDED:
            raise ValueError('Package exceeds expanded limits.')
        names=set(); folded=set()
        for e in entries:
            _relative(e.filename)
            if not re.fullmatch(r'[A-Za-z0-9_./-]+', e.filename):
                raise ValueError('Package path is not portable.')
            mode=e.external_attr >> 16
            if (e.is_dir() or e.flag_bits & 1 or e.file_size>MAX_FILE or
                    stat.S_IFMT(mode) not in (0,stat.S_IFREG) or
                    e.filename in names or e.filename.casefold() in folded):
                raise ValueError('Package has unsupported or duplicate entries.')
            names.add(e.filename);folded.add(e.filename.casefold())
        if 'SOURCE_MANIFEST.json' not in names:
            raise ValueError('Source manifest missing.')
        raw=archive.read('SOURCE_MANIFEST.json')
        if len(raw)>2*1024*1024:raise ValueError('Source manifest exceeds limit.')
        manifest=json.loads(raw,object_pairs_hook=_unique_object)
        if not isinstance(manifest,dict) or set(manifest)!={'source_tree_sha256','files'}:
            raise ValueError('Invalid source manifest fields.')
        files=manifest['files']
        if not isinstance(files,dict) or not files:raise ValueError('Empty source manifest.')
        for name,digest in files.items():
            path=_relative(name);_sha(digest)
            # Reject common operational payloads, even when listed in the manifest.
            parts={p.casefold() for p in path.parts}
            if (_private(path) or path.name.casefold() in {'.env','id_rsa','id_ed25519','credentials.json','storage_state.json'} or
                    path.suffix.casefold() in {'.db','.sqlite','.sqlite3','.pem','.key'} or
                    path.name.casefold().endswith(('-wal','-shm')) or
                    parts & {'__pycache__','.git','.venv','node_modules','private','secrets'}):
                raise ValueError('Operational/private payload is not a source release.')
            if _digest(archive.read('chief-agent/'+name))!=digest:
                raise ValueError('Source file checksum differs.')
        if names!={'SOURCE_MANIFEST.json',*('chief-agent/'+n for n in files)}:
            raise ValueError('Package contains unlisted payloads.')
        tree=_digest('\n'.join(n+'\0'+files[n] for n in sorted(files)).encode())
        if tree!=manifest['source_tree_sha256'] or tree!=expected_source:
            raise ValueError('Source tree identity differs.')
    return {'archive_sha256':expected_archive,'source_tree_sha256':tree,'file_count':len(files)}


def stage_package(data, root, *, expected_archive, expected_source, private_storage_confirmed=False):
    """Write verified archive and completion receipt exclusively in a new slot.

    No release manifest, regression result or backup approval is inferred here.
    A crash without a receipt leaves an inert incomplete slot, never auto-retried.
    The running installation and database are not arguments to this API.
    """
    if private_storage_confirmed is not True:
        raise ValueError('Confirm operator-owned private staging storage first.')
    verified=inspect_package(data,expected_archive=expected_archive,expected_source=expected_source)
    root=Path(os.path.abspath(root));identity=uuid.uuid4().hex
    receipt={**verified,'stage_id':identity,'status':'STAGED_NOT_INSTALLED',
             'created_at':utc_text(utc_now()),'activation':'NOT_IMPLEMENTED',
             'compatibility':'NOT_EVALUATED','data_migration':False}
    payloads={'source.zip':data,'receipt.json':json.dumps(receipt,sort_keys=True).encode()}
    created=[]
    with ExitStack() as stack:
        if os.name=='nt':
            current=Path(root.anchor)
            stack.enter_context(_windows_directory(current))
            for part in root.parts[1:]:
                current/=part;stack.enter_context(_windows_directory(current))
            folder=root/identity;folder.mkdir(mode=0o700)
            leaf=ExitStack()
            try:
                leaf.enter_context(_windows_directory(folder))
                def write(name):return os.open(folder/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_BINARY,0o600)
                _write(payloads,created,write,None)
            except BaseException:
                for name in reversed(created):(folder/name).unlink()
                leaf.close();folder.rmdir();raise
            finally:leaf.close()
        else:
            flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
            fd=os.open(root.anchor,flags);stack.callback(os.close,fd)
            for part in root.parts[1:]:
                fd=os.open(part,flags,dir_fd=fd);stack.callback(os.close,fd)
            parent=fd;os.mkdir(identity,0o700,dir_fd=parent)
            leaf=None
            try:
                leaf=os.open(identity,flags,dir_fd=parent)
                def write(name):return os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=leaf)
                _write(payloads,created,write,leaf);os.fsync(parent)
            except BaseException:
                for name in reversed(created):os.unlink(name,dir_fd=leaf)
                os.rmdir(identity,dir_fd=parent);raise
            finally:
                if leaf is not None:os.close(leaf)
    return receipt


def _write(payloads,created,open_file,directory_fd):
    for name,data in payloads.items():
        fd=open_file(name);created.append(name)
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
    if directory_fd is not None:os.fsync(directory_fd)
