"""Create one exclusive inert tree; never follow directory links or overwrite.

The existing root must be private and operator controlled. Windows privacy is
an installation ACL prerequisite, not something mode=0700 proves. Directory
handles prevent rename/reparse substitution while writing. Same-account or
administrator compromise is outside this boundary. A process crash may leave
an incomplete inert slot; it is never reused or automatically activated.
"""
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path, PurePosixPath

from operations.backup import _relative
from operations.draft_files import _windows_directory, validate_application_id


@contextmanager
def private_tree(root, identity):
    root=Path(os.path.abspath(root));validate_application_id(identity)
    with ExitStack() as ancestors:
        current=Path(root.anchor)
        if os.name=='nt':
            ancestors.enter_context(_windows_directory(current))
            for part in root.parts[1:]:
                current/=part;ancestors.enter_context(_windows_directory(current))
            parent=None
        else:
            flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
            parent=os.open(current,flags);ancestors.callback(os.close,parent)
            for part in root.parts[1:]:
                parent=os.open(part,flags,dir_fd=parent);ancestors.callback(os.close,parent)
            info=os.fstat(parent)
            if info.st_uid!=os.getuid() or info.st_mode & 0o077:
                raise ValueError('Installation root must be private and owned by this user.')
        folder=root/identity
        if os.name=='nt':folder.mkdir(mode=0o700)
        else:os.mkdir(identity,0o700,dir_fd=parent)
        directories={};created_files=[];created_dirs=[]
        handles=ExitStack()
        try:
            if os.name=='nt':
                handles.enter_context(_windows_directory(folder));directories['']=None
            else:
                directories['']=os.open(identity,flags,dir_fd=parent)
                handles.callback(os.close,directories[''])
                os.fsync(parent)

            def directory(parts):
                key=''
                for part in parts:
                    previous=key;key='/'.join(filter(None,(key,part)))
                    if key in directories:continue
                    if os.name=='nt':
                        (folder/key).mkdir(mode=0o700);created_dirs.append(key)
                        handles.enter_context(_windows_directory(folder/key));directories[key]=None
                    else:
                        os.mkdir(part,0o700,dir_fd=directories[previous]);created_dirs.append(key)
                        fd=os.open(part,flags,dir_fd=directories[previous]);directories[key]=fd
                        handles.callback(os.close,fd)
                return key

            def write(name,data):
                path=_relative(name)
                if not isinstance(data,bytes):raise ValueError('File payload must be bytes.')
                key=directory(path.parts[:-1])
                if os.name=='nt':
                    fd=os.open(folder/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_BINARY,0o600)
                else:
                    fd=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directories[key])
                created_files.append((key,path.name))
                with os.fdopen(fd,'wb') as stream:
                    if stream.write(data)!=len(data):raise OSError('Incomplete file write.')
                    stream.flush();os.fsync(stream.fileno())

            yield folder,write
            if os.name!='nt':
                for fd in reversed(list(directories.values())):os.fsync(fd)
                os.fsync(parent)
        except BaseException:
            for key,name in reversed(created_files):
                if os.name=='nt':(folder/key/name).unlink()
                else:os.unlink(name,dir_fd=directories[key])
            if os.name=='nt':
                handles.close()
                for key in reversed(created_dirs):(folder/key).rmdir()
                folder.rmdir()
            else:
                for key in reversed(created_dirs):
                    path=PurePosixPath(key);previous='' if str(path.parent)=='.' else str(path.parent)
                    os.rmdir(path.name,dir_fd=directories[previous])
                os.rmdir(identity,dir_fd=parent)
            raise
        finally:
            handles.close()
