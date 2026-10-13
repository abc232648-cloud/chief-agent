"""Existing private update state only; no creation, migration or restore unlock."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from operations.backup import _plain
from operations.restore_guard import assert_not_quarantined
from operations.storage_health import configure_connection


class ExistingUpdateStore:
    def __init__(self, database):
        self.path = Path(database)
        if not self.path.is_absolute():
            raise ValueError('Absolute existing database required.')

    @contextmanager
    def _connect(self):
        _plain(self.path, file=True)
        assert_not_quarantined(self.path)
        con = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            configure_connection(con)
            # Update receipts never need DDL or another attached database.
            allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_INSERT,
                       sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_FUNCTION}
            def authorize(action, table, column, database, trigger):
                if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE} and table != 'control_state':
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
            con.set_authorizer(authorize)
            with con:
                yield con
        finally:
            con.close()
