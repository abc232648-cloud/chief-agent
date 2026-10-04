"""Fail closed when a source/evidence package includes model credential storage."""
from pathlib import PurePosixPath


def check_archive_names(names):
    for name in names:
        path = PurePosixPath(str(name).replace('\\', '/'))
        if ('model-credentials' in {p.lower() for p in path.parts}
                or path.suffix.lower() in {'.dpapi', '.cred'}):
            raise ValueError('Private model credential storage must not enter source/evidence archives.')
