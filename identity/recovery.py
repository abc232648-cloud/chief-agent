"""Local administrative Owner recovery. No schema creation or quarantine bypass."""
import ctypes
import os
from pathlib import Path
import sqlite3
import stat

from database.identity_migrations import schema_ready
from database.store import ClosingConnection
from operations.restore_guard import assert_not_quarantined, QUARANTINE_APPLICATION_ID
from operations.time_integrity import utc_now, utc_text
from .passwords import password_hash


def is_os_administrator():
    return bool(ctypes.windll.shell32.IsUserAnAdmin()) if os.name=='nt' else os.geteuid()==0


def recover_owner(database, username, expected_owner_id, password):
    if not is_os_administrator():
        raise PermissionError('Owner recovery requires explicit operating-system administrator authorization.')
    path=Path(database)
    if not path.is_absolute():raise ValueError('Use the exact absolute database path.')
    for parent in (path,*path.parents):
        if parent.is_symlink() or (hasattr(parent,'is_junction') and parent.is_junction()):
            raise PermissionError('Recovery target must not use links or junctions.')
    target=path.resolve(strict=True);before=target.stat()
    if not stat.S_ISREG(before.st_mode):raise ValueError('An existing regular database is required.')
    assert_not_quarantined(target)
    for sidecar in (Path(str(target)+'-wal'),Path(str(target)+'-shm')):
        if sidecar.is_symlink() or (hasattr(sidecar,'is_junction') and sidecar.is_junction()):
            raise PermissionError('Recovery sidecars must not be links or junctions.')
    # mode=rw never creates a missing database. Do not construct Store, whose
    # historical initialization has DDL side effects.
    with sqlite3.connect(target.as_uri()+'?mode=rw',uri=True,factory=ClosingConnection) as con:
        con.row_factory=sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON');con.execute('PRAGMA busy_timeout=1000')
        con.execute('BEGIN IMMEDIATE')
        if con.execute('PRAGMA application_id').fetchone()[0]==QUARANTINE_APPLICATION_ID:
            raise PermissionError('Quarantined restores cannot be recovered or promoted here.')
        if not schema_ready(con):raise ValueError('An existing accepted identity schema is required.')
        row=con.execute('SELECT id,role,username FROM human_identities WHERE id=?',(expected_owner_id,)).fetchone()
        if not row or row['username']!=username or row['role']!='Owner':
            raise PermissionError('Exact Owner identity and target must match; other roles cannot be elevated.')
        current=target.stat()
        if (before.st_dev,before.st_ino)!=(current.st_dev,current.st_ino):raise PermissionError('Recovery target changed.')
        encoded=password_hash(password)
        con.execute('UPDATE human_identities SET password_hash=?,enabled=1 WHERE id=?',(encoded,row['id']))
        revoked=con.execute('UPDATE human_sessions SET revoked=1 WHERE human_id=? AND revoked=0',(row['id'],)).rowcount
        from .service import digest
        con.execute('DELETE FROM human_login_limits WHERE username_hash=?',(digest(username),))
        authority='OS_ADMIN_WINDOWS' if os.name=='nt' else 'OS_ADMIN_UID:'+str(os.geteuid())
        con.execute('INSERT INTO human_security_events(human_id,session_id,operation,domain,resource,outcome,authority,received_at) VALUES(?,NULL,?,NULL,?,?,?,?)',
            (row['id'],'OWNER_RECOVERED',row['id'],'RECORDED',authority,utc_text(utc_now())))
    # Administrative recovery must not leave new WAL/SHM files owned by root.
    if os.name!='nt' and os.geteuid()==0:
        for item in (target,Path(str(target)+'-wal'),Path(str(target)+'-shm')):
            if item.exists():
                if item.is_symlink():raise PermissionError('Unexpected recovery sidecar link.')
                os.chown(item,before.st_uid,before.st_gid,follow_symlinks=False)
    return {'status':'OWNER_RECOVERED','owner_id':expected_owner_id,'sessions_revoked':revoked,'schema_migration':False}
