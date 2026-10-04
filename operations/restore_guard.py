"""A quarantine tripwire, not an OS sandbox or protection against an operator.

Do not provide an automatic unlock: external outcomes need reconciliation first.
The application_id marker travels with a database copied without its sidecar.
"""
from pathlib import Path

QUARANTINE_APPLICATION_ID = 0x43485251  # CHRQ; only written on isolated restores.


class RestoreQuarantined(PermissionError):
    pass


def marker_path(database):
    return Path(str(Path(database)) + '.restore-lock.json')


def assert_not_quarantined(database):
    path = Path(database).resolve()
    # Existence is sufficient: malformed markers must also fail closed.
    if marker_path(path).exists() or marker_path(path).is_symlink():
        raise RestoreQuarantined('Isolated restore is quarantined; operational reconciliation is required.')
    if path.is_file():
        with path.open('rb') as stream:
            header = stream.read(100)
        if header.startswith(b'SQLite format 3\x00') and int.from_bytes(header[68:72], 'big') == QUARANTINE_APPLICATION_ID:
            raise RestoreQuarantined('Restored database is quarantined; automatic replay is prohibited.')
