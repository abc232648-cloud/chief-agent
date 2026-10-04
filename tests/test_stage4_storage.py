import os
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import pytest
from database.store import Store
from operations import storage_health as health


def test_effective_durability_and_no_schema_change(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    with store._connect() as con:
        before=list(con.execute('SELECT sql FROM sqlite_master ORDER BY name'))
        assert health.configure_connection(con)==health.effective_settings(con)
        assert health.effective_settings(con)==dict(journal_mode='wal',synchronous=2,foreign_keys=1,busy_timeout=10000,wal_autocheckpoint=1000)
        assert [tuple(r) for r in before]==[tuple(r) for r in con.execute('SELECT sql FROM sqlite_master ORDER BY name')]


def test_contended_writer_fails_without_partial_rows_then_recovers(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    with store._connect() as first, store._connect() as second:
        first.execute('BEGIN IMMEDIATE');first.execute("INSERT INTO commands(instruction) VALUES('committed')")
        second.execute('PRAGMA busy_timeout=20')
        with pytest.raises(sqlite3.OperationalError,match='locked'):
            second.execute("INSERT INTO commands(instruction) VALUES('must-not-exist')")
    assert [r['instruction'] for r in store.commands()]==['committed']
    assert store.queue_command('after contention')


def test_sqlite_page_limit_disk_full_rolls_back_transaction(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    with store._connect() as con:
        pages=con.execute('PRAGMA page_count').fetchone()[0]
        con.execute('PRAGMA max_page_count='+str(pages))
        with pytest.raises(sqlite3.OperationalError,match='full'):
            con.execute('BEGIN IMMEDIATE')
            con.execute('INSERT INTO commands(instruction) VALUES(?)',('x'*2_000_000,))
        con.rollback()
        assert con.execute('SELECT COUNT(*) FROM commands').fetchone()[0]==0
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_read_only_sqlite_refuses_write_preserves_history(tmp_path):
    store=Store(tmp_path/'db.sqlite');store.queue_command('preserved')
    con=sqlite3.connect(store.path.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError,match='readonly'):
            con.execute("DELETE FROM commands")
    finally:con.close()
    assert store.commands()[0]['instruction']=='preserved'


def test_actual_process_crash_discards_uncommitted_wal_write(tmp_path):
    store=Store(tmp_path/'db.sqlite');store.queue_command('before')
    script="from database.store import Store;import os,sys;s=Store(sys.argv[1]);c=s._connect();c.execute('BEGIN IMMEDIATE');c.execute(\"INSERT INTO commands(instruction) VALUES('uncommitted')\");os._exit(19)"
    result=subprocess.run([sys.executable,'-c',script,str(store.path)],capture_output=True,timeout=15)
    assert result.returncode==19
    assert [r['instruction'] for r in store.commands()]==['before']
    with store._connect() as con:assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


@pytest.mark.parametrize('failure',['space','create','fsync'])
def test_readiness_failure_is_closed_and_probe_removed(tmp_path,monkeypatch,failure):
    def fail(*args,**kwargs):raise OSError('synthetic storage failure')
    if failure=='space':monkeypatch.setattr(health.shutil,'disk_usage',lambda p:SimpleNamespace(free=0))
    elif failure=='create':monkeypatch.setattr(health.tempfile,'mkstemp',fail)
    else:monkeypatch.setattr(health.os,'fsync',fail)
    with pytest.raises(OSError):health.storage_readiness(tmp_path)
    assert not list(tmp_path.glob('.chief-storage-probe-*'))


def test_readiness_positive_leaves_no_file(tmp_path):
    assert health.storage_readiness(tmp_path)['status']=='READY'
    assert not list(tmp_path.glob('.chief-storage-probe-*'))


def test_failed_connection_setup_closes_descriptor(tmp_path,monkeypatch):
    store=Store(tmp_path/'db.sqlite');connections=[]
    connect=sqlite3.connect
    def capture(*args,**kwargs):
        con=connect(*args,**kwargs);connections.append(con);return con
    def fail(con):raise sqlite3.OperationalError('synthetic pragma failure')
    monkeypatch.setattr(sqlite3,'connect',capture);monkeypatch.setattr(health,'configure_connection',fail)
    with pytest.raises(sqlite3.OperationalError):store._connect()
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):connections[0].execute('SELECT 1')
