"""Local SQLite durability policy and bounded startup readiness (not certification)."""
import os
from pathlib import Path
import shutil
import tempfile
from operations.backup import _plain

MIN_FREE_BYTES = 64 * 1024 * 1024


def configure_connection(con):
    con.execute('PRAGMA busy_timeout=10000')
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA synchronous=FULL')
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA wal_autocheckpoint=1000')
    actual = effective_settings(con)
    if actual != {'journal_mode':'wal', 'synchronous':2, 'foreign_keys':1,
                  'busy_timeout':10000, 'wal_autocheckpoint':1000}:
        raise RuntimeError('Required SQLite durability settings unavailable.')
    return actual


def effective_settings(con):
    return {key:con.execute('PRAGMA '+key).fetchone()[0] for key in
            ('journal_mode','synchronous','foreign_keys','busy_timeout','wal_autocheckpoint')}


def storage_readiness(directory):
    """Probe private state filesystem; never touch the database or truncate history."""
    root=Path(directory);_plain(root)
    free=shutil.disk_usage(root).free
    if free < MIN_FREE_BYTES:
        raise OSError('Insufficient free space for service startup.')
    fd,name=tempfile.mkstemp(prefix='.chief-storage-probe-',dir=root)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(b'chief-storage-probe-v1\n');stream.flush();os.fsync(stream.fileno())
    finally:
        Path(name).unlink(missing_ok=True)
    return {'status':'READY','free_bytes':free,'minimum_free_bytes':MIN_FREE_BYTES,
            'scope':'STARTUP_PROBE_ONLY'}
