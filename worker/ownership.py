"""Single-machine process ownership. Never unlink the lock inode on release."""
import os
from pathlib import Path
from operations.backup import _plain


class WorkerAlreadyRunning(RuntimeError):pass


class ComponentOwnership:
    def __init__(self,database,component):
        if component not in {'worker','scheduler','scheduler-cycle','dashboard','approved-action'}:raise ValueError('Unknown component ownership.')
        path=Path(database).absolute();_plain(path.parent)
        if path.exists():_plain(path,file=True)
        self.path=path.with_name(path.name+'.'+component+'.lock');self.fd=None

    def __enter__(self):
        if self.path.exists():_plain(self.path,file=True)
        fd=os.open(self.path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            if os.name=='nt':
                import msvcrt
                # Locking beyond EOF is supported; writing must happen only after ownership.
                msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            os.write(fd,b'1');os.fsync(fd);self.fd=fd
            return self
        except OSError:
            os.close(fd)
            raise WorkerAlreadyRunning('Another process owns this component; recovery was not attempted.') from None

    def __exit__(self,*args):
        if self.fd is not None:
            # Closing the descriptor releases the OS lock even after process termination.
            os.close(self.fd);self.fd=None


class WorkerOwnership(ComponentOwnership):
    def __init__(self,database):super().__init__(database,'worker')
