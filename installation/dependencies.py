"""Strict offline dependency inputs. No resolution, download or installation.

Only exact pins with SHA256 hashes are accepted. A wheel bundle must contain
one reviewed wheel per lock entry, no other files, and matching wheel metadata.
This authenticates bytes against caller pins, not their publisher or safety.
"""
import hashlib
import io
import re
import stat
import zipfile
from email.parser import BytesParser

from operations.backup import _relative, _sha

MAX_BUNDLE=256*1024*1024
MAX_WHEEL=64*1024*1024
MAX_WHEEL_MEMBER=128*1024*1024
MAX_WHEEL_EXPANDED=256*1024*1024


def normalized(name):
    return re.sub(r'[-_.]+','-',name).lower()


def locked_requirements(text):
    if not isinstance(text,str) or len(text)>1024*1024:raise ValueError('Dependency lock exceeds limit.')
    logical=[];pending=''
    for line in text.splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        continued=line.endswith('\\')
        pending+=' '+(line[:-1].strip() if continued else line)
        if not continued:logical.append(pending.strip());pending=''
    if pending:raise ValueError('Incomplete dependency lock line.')
    result={}
    for line in logical:
        parts=line.split()
        match=re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)',parts[0])
        if not match or len(parts)<2:raise ValueError('Dependencies require exact versions and hashes only.')
        name,version=match.groups();name=normalized(name)
        if name in result:raise ValueError('Duplicate dependency.')
        hashes=[]
        for item in parts[1:]:
            if not item.startswith('--hash=sha256:'):raise ValueError('Unsafe dependency option or URL.')
            digest=item[len('--hash=sha256:'):];_sha(digest);hashes.append(digest)
        result[name]={'version':version,'hashes':set(hashes)}
    if not result or len(result)>200:raise ValueError('Dependency lock is empty or exceeds limit.')
    return result


def inspect_wheels(data, *, expected_archive, lock_text):
    _sha(expected_archive)
    if not isinstance(data,bytes) or len(data)>MAX_BUNDLE or hashlib.sha256(data).hexdigest()!=expected_archive:
        raise ValueError('Dependency bundle identity or size differs.')
    locked=locked_requirements(lock_text);seen=set();wheels={};expanded=0
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        entries=bundle.infolist()
        if len(entries)!=len(locked) or sum(e.file_size for e in entries)>MAX_BUNDLE:raise ValueError('Wheel count or expanded bundle size does not match limits.')
        if len({e.filename.casefold() for e in entries})!=len(entries):raise ValueError('Duplicate wheel filename.')
        for entry in bundle.infolist():
            path=_relative(entry.filename)
            if len(path.parts)!=1 or not re.fullmatch(r'[A-Za-z0-9_.+-]+\.whl',entry.filename) or entry.flag_bits&1 or entry.file_size>MAX_WHEEL or stat.S_IFMT(entry.external_attr>>16) not in (0,stat.S_IFREG):
                raise ValueError('Unsupported wheel bundle entry.')
            raw=bundle.read(entry)
            if len(raw)!=entry.file_size:raise ValueError('Incomplete wheel.')
            with zipfile.ZipFile(io.BytesIO(raw)) as wheel:
                names=set();metadata=[]
                if len(wheel.infolist())>20000:raise ValueError('Wheel has too many files.')
                for info in wheel.infolist():
                    value=info.filename[:-1] if info.is_dir() else info.filename
                    rel=_relative(value)
                    if value.casefold() in names or info.flag_bits&1 or stat.S_IFMT(info.external_attr>>16) not in (0,stat.S_IFREG,stat.S_IFDIR):
                        raise ValueError('Wheel contains duplicate, encrypted or linked entries.')
                    names.add(value.casefold());expanded+=info.file_size
                    # Compressed archive and expanded member limits are distinct:
                    # the pinned Playwright wheel contains a ~90 MiB Node binary.
                    if info.file_size>MAX_WHEEL_MEMBER or expanded>MAX_WHEEL_EXPANDED:raise ValueError('Wheel expansion exceeds limit.')
                    # Installation must not create interpreter startup hooks.
                    if rel.suffix.casefold()=='.pth' or rel.name.casefold() in {'sitecustomize.py','usercustomize.py'}:
                        raise ValueError('Interpreter startup hooks require a separate reviewed policy.')
                    if not info.is_dir() and len(rel.parts)==2 and rel.parts[0].endswith('.dist-info') and rel.name=='METADATA':
                        if info.file_size>1024*1024:raise ValueError('Wheel metadata exceeds limit.')
                        metadata.append(wheel.read(info))
                if len(metadata)!=1:raise ValueError('Wheel requires one distribution metadata record.')
                info=BytesParser().parsebytes(metadata[0]);names=info.get_all('Name',[]);versions=info.get_all('Version',[])
                if len(names)!=1 or len(versions)!=1:raise ValueError('Ambiguous wheel identity.')
                name=normalized(names[0]);pin=locked.get(name)
                if name in seen or pin is None or versions[0]!=pin['version'] or hashlib.sha256(raw).hexdigest() not in pin['hashes']:
                    raise ValueError('Wheel does not match reviewed dependency pin.')
                seen.add(name);wheels[entry.filename]=raw
    if seen!=set(locked):raise ValueError('Missing locked dependencies.')
    return wheels
