import sqlite3

import pytest

from operations.restore_guard import marker_path, RestoreQuarantined
from tests.test_update_history import store
from update_center.storage import ExistingUpdateStore


def test_update_store_never_creates_database(tmp_path):
    target = tmp_path / 'absent.sqlite3'
    with pytest.raises((ValueError, FileNotFoundError)):
        with ExistingUpdateStore(target)._connect():
            pytest.fail('Missing database opened')
    assert not target.exists()


def test_restore_quarantine_is_not_bypassed(store):
    marker_path(store.path).write_text('{}', encoding='utf-8')
    with pytest.raises(RestoreQuarantined):
        with ExistingUpdateStore(store.path)._connect():
            pytest.fail('Quarantined database opened')


@pytest.mark.parametrize('statement', [
    'CREATE TABLE unwanted(id TEXT)', 'DROP TABLE control_state',
    'DELETE FROM control_state', 'PRAGMA user_version=123',
    "ATTACH DATABASE ':memory:' AS extra", "INSERT INTO actions(name,action) VALUES('x','x')",
])
def test_receipt_connection_cannot_migrate_or_modify_domain_records(store, statement):
    with ExistingUpdateStore(store.path)._connect() as con:
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(statement)


def test_update_receipts_use_full_durability(store):
    # Read effective connection setup before applying the SQL restriction.
    with ExistingUpdateStore(store.path)._connect() as con:
        con.set_authorizer(None)
        assert con.execute('PRAGMA synchronous').fetchone()[0] == 2
        assert con.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
