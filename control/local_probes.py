"""Bounded local observations, never provider calls, repairs or qualifications."""
from contextlib import closing
from pathlib import Path
import os
import sqlite3

from operations.backup import _plain
from operations.time_integrity import utc_text
from .health_contracts import HealthStatus as H, Observation


def database(store, now):
    stamp = utc_text(now())
    try:
        path = Path(os.path.abspath(store.path))
        _plain(path, file=True)
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0.25)) as con:
            con.execute('PRAGMA query_only=ON')
            # Read the actual schema page; SELECT 1 alone does not touch database contents.
            con.execute('SELECT count(*) FROM sqlite_master').fetchone()
        return Observation(H.HEALTHY, stamp, stamp, 'local-database-read',
                           'Database responded to a read. Writes, full integrity and recovery are not tested here.')
    except (OSError, ValueError, sqlite3.Error):
        return Observation(H.UNAVAILABLE, stamp, stamp, 'local-database-read',
                           'Database read could not be completed. No repair was attempted.')


def service(store, component, now):
    from deployment.service_runtime import read_record
    stamp = utc_text(now())
    root = Path(os.environ.get('CHIEF_STATE_ROOT', str(Path(store.path).absolute().parent)))
    try:
        if not root.is_absolute():
            raise ValueError('Unusable state root')
        record = read_record(root / 'service-control' / (component + '.json'))
        if not isinstance(record, dict) or record.get('version') != 1 or record.get('component') != component:
            raise ValueError('Unusable service record')
        state = record.get('state')
        # Old status files were written only on transitions: they are not live heartbeats.
        observed = record.get('heartbeat_at')
        if not isinstance(observed, str):
            observed = None
        status = {'READY': H.HEALTHY, 'STARTING': H.DEGRADED, 'STOPPING': H.DEGRADED,
                  'STOPPED': H.UNAVAILABLE, 'REVIEW': H.UNAVAILABLE}.get(state, H.UNKNOWN)
        reason = {'READY': 'Service supervisor is reporting; task progress is not established.',
                  'STARTING': 'Service is starting.', 'STOPPING': 'Service is stopping.',
                  'STOPPED': 'Service reported that it stopped.', 'REVIEW': 'Service stopped with a problem; review is needed.'}.get(state, 'Service state is not established.')
        return Observation(status, observed, stamp, 'local-' + component + '-heartbeat', reason)
    except (OSError, ValueError, TypeError):
        return Observation(H.UNKNOWN, None, stamp, 'local-' + component + '-heartbeat',
                           'No usable service heartbeat is available. The service may not have been started through its launcher.')
