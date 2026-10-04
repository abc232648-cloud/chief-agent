import gc
import os
import sqlite3
import pytest
from database.store import Store

def test_transaction_context_commits_and_closes(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    with store._connect() as con:
        con.execute("INSERT INTO commands(instruction) VALUES('committed')")
    with pytest.raises(sqlite3.ProgrammingError):con.execute('SELECT 1')
    assert store.commands()[0]['instruction']=='committed'

def test_transaction_context_rolls_back_and_closes(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    with pytest.raises(RuntimeError):
        with store._connect() as con:
            con.execute("INSERT INTO commands(instruction) VALUES('rollback')")
            raise RuntimeError('rollback test')
    with pytest.raises(sqlite3.ProgrammingError):con.execute('SELECT 1')
    assert store.commands()==[]

def test_repeated_reads_do_not_depend_on_garbage_collection(tmp_path):
    if not os.path.isdir('/proc/self/fd'):pytest.skip('Linux descriptor accounting')
    store=Store(tmp_path/'db.sqlite')
    gc.collect();gc.disable()
    try:
        before=len(os.listdir('/proc/self/fd'))
        for _ in range(200):store.counts()
        assert len(os.listdir('/proc/self/fd'))<=before+1
    finally:gc.enable()
