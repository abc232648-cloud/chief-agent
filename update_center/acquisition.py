"""Bounded HTTPS release acquisition into operator-owned private staging.

Acquisition grants no trust or installation authority. The downloaded envelope
and archive must still pass the existing publisher verification and acceptance
pipeline before any source can be selected.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import uuid

from operations import backup
from update_center.release_trust import MAX_ENVELOPE
from update_center.staging import MAX_ARCHIVE


class AcquisitionError(RuntimeError):
    pass


def _url(value, allowed_hosts):
    parsed = urlparse(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or
            parsed.fragment or parsed.query or parsed.hostname not in allowed_hosts):
        raise ValueError('Release URL must be credential-free HTTPS on an approved host.')
    return value


def _download(url, *, allowed_hosts, limit, timeout):
    _url(url, allowed_hosts)
    request = Request(url, headers={'User-Agent': 'Chief-Release-Acquisition/1', 'Accept': 'application/octet-stream'})
    with urlopen(request, timeout=timeout) as response:
        _url(response.geturl(), allowed_hosts)
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise AcquisitionError('Release download exceeded its size limit.')
    return raw


def _write(path, payload):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            fd = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if fd is not None:
            os.close(fd)


def acquire_release(archive_url, envelope_url, destination, *, allowed_hosts,
                    private_storage_confirmed=False, timeout=30):
    """Download an inert release pair to a new private directory.

    The caller supplies the host allow-list from local operator policy. Query
    strings and embedded credentials are rejected so release acquisition cannot
    become a secret-bearing generic downloader.
    """
    if private_storage_confirmed is not True:
        raise ValueError('Private release staging storage must be confirmed.')
    hosts = frozenset(allowed_hosts)
    if not hosts or any(not isinstance(item, str) or not item for item in hosts):
        raise ValueError('At least one explicit release host is required.')
    root = backup._plain(Path(destination))
    identity = uuid.uuid4().hex
    folder = root / ('download-' + identity)
    folder.mkdir(mode=0o700)
    try:
        envelope = _download(envelope_url, allowed_hosts=hosts, limit=MAX_ENVELOPE, timeout=timeout)
        archive = _download(archive_url, allowed_hosts=hosts, limit=MAX_ARCHIVE, timeout=timeout)
        envelope_path = folder / 'release-envelope.json'
        archive_path = folder / 'release.zip'
        _write(envelope_path, envelope)
        _write(archive_path, archive)
        return {
            'status': 'DOWNLOADED_NOT_VERIFIED',
            'directory': str(folder),
            'archive': str(archive_path),
            'envelope': str(envelope_path),
            'archive_sha256': hashlib.sha256(archive).hexdigest(),
            'archive_size': len(archive),
            'envelope_sha256': hashlib.sha256(envelope).hexdigest(),
            'activation_authorized': False,
        }
    except BaseException:
        for name in ('release.zip', 'release-envelope.json'):
            path = folder / name
            if path.exists() and not path.is_symlink():
                path.unlink()
        try:
            folder.rmdir()
        except OSError:
            pass
        raise
