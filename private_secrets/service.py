import json
import os
from pathlib import Path
import re
import stat
from operations.time_integrity import aware_utc,utc_now


class SecretUnavailable(ValueError):
    pass


def _private_read(root,key):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,96}',key) or key in {'.','..'}:raise SecretUnavailable('Secret unavailable.')
    root=Path(root)
    if not root.is_absolute():raise SecretUnavailable('Secret unavailable.')
    for item in (root,*root.parents):
        if item.is_symlink() or (hasattr(item,'is_junction') and item.is_junction()):raise SecretUnavailable('Secret unavailable.')
    path=root/key
    if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()):raise SecretUnavailable('Secret unavailable.')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>32768:raise SecretUnavailable('Secret unavailable.')
        if os.name!='nt' and (info.st_mode&0o077 or info.st_uid not in {0,os.geteuid()}):raise SecretUnavailable('Secret unavailable.')
        return stream.read(32769)


class SecretReferences:
    def __init__(self,references,*,credential_directory=None,windows_directory=None,now=utc_now):
        self.references=references;self.credential_directory=credential_directory
        self.windows_directory=windows_directory;self.now=now

    def resolve(self,identity,*,consumer,domain):
        try:
            ref=self.references[identity]
            if not isinstance(ref.get('consumers'),list) or not all(isinstance(item,str) for item in ref['consumers']):raise ValueError()
            if ref.get('revoked') is not False or ref['domain']!=domain or consumer not in ref['consumers']:
                raise SecretUnavailable('Secret unavailable for this consumer.')
            if not isinstance(ref['version'],str) or not ref['version']:raise ValueError()
            if ref.get('expires_at') is not None and aware_utc(ref['expires_at'])<=self.now():raise ValueError()
            if ref['backend']=='systemd':
                if os.name=='nt' or not self.credential_directory:raise ValueError()
                data=_private_read(self.credential_directory,ref['key'])
            elif ref['backend']=='windows-dpapi':
                if os.name!='nt' or not self.windows_directory:raise ValueError()
                from .windows import unprotect
                data=unprotect(_private_read(self.windows_directory,ref['key']))
            else:raise ValueError()
            if not data or len(data)>16384 or b'\0' in data:raise ValueError()
            return data.decode('utf-8')
        except (KeyError,TypeError,ValueError,OSError):
            raise SecretUnavailable('Secret unavailable for this consumer.') from None


def resolve_configured(environment,name,*,consumer,domain):
    reference=environment.get(name+'_REF')
    production=os.environ.get('CHIEF_INSTANCE_MODE','').lower()=='production'
    if reference:
        if environment.get(name):raise SecretUnavailable('Choose a secret reference; ambiguous plaintext configuration is refused.')
        try:
            file=Path(os.environ['CHIEF_SECRET_REFERENCES'])
            refs=json.loads(_private_read(file.parent,file.name))
            service=SecretReferences(refs,credential_directory=os.environ.get('CREDENTIALS_DIRECTORY'),windows_directory=os.environ.get('CHIEF_SECRET_ROOT'))
            return service.resolve(reference,consumer=consumer,domain=domain)
        except (KeyError,ValueError,OSError):raise SecretUnavailable('Secret reference configuration is unavailable.') from None
    if production:raise SecretUnavailable('Production credentials require an explicit secret reference.')
    return environment.get(name,'') # Explicit legacy/development compatibility only.
