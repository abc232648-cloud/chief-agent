"""Durable publisher-verification receipts, separate from installation authority.

Uses the existing control_state table, without DDL. A restored installation must
remain quarantined: this database is not a remote, rollback-resistant trust log.
"""
import json
import re

from compatibility.manifest import _unique_object
from operations.time_integrity import aware_utc
from update_center.release_trust import ReleaseRejected, verify_release

HEAD = 'update_release_v1:head'
PREFIX = 'update_release_v1:receipt:'
FIELDS = {'version', 'sequence', 'envelope_sha256', 'archive_sha256', 'source_sha256',
          'commit', 'key_id', 'verified_at', 'expires_at'}


def _load(raw):
    value = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError()
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError()
    if type(value['sequence']) is not int or value['sequence'] < 1:
        raise ValueError()
    for field in ('envelope_sha256', 'archive_sha256', 'source_sha256'):
        if not isinstance(value[field], str) or not re.fullmatch('[0-9a-f]{64}', value[field]):
            raise ValueError()
    if not isinstance(value['commit'], str) or not re.fullmatch('[0-9a-f]{40}', value['commit']):
        raise ValueError()
    if not isinstance(value['key_id'], str) or not re.fullmatch('[a-z0-9][a-z0-9._-]{0,63}', value['key_id']):
        raise ValueError()
    if not aware_utc(value['verified_at']) < aware_utc(value['expires_at']):
        raise ValueError()
    return value


def _head(con):
    row = con.execute('SELECT value FROM control_state WHERE key=?', (HEAD,)).fetchone()
    if row is None:
        # Missing head beside older receipts is corruption, not a fresh install.
        if con.execute('SELECT 1 FROM control_state WHERE substr(key,1,?)=? LIMIT 1',
                       (len(PREFIX), PREFIX)).fetchone():
            raise ValueError()
        return None
    value = _load(row[0])
    receipt = con.execute('SELECT value FROM control_state WHERE key=?', (PREFIX + str(value['sequence']),)).fetchone()
    if not receipt or _load(receipt[0]) != value:
        raise ValueError()
    return value


def verify_and_record(store, envelope, keys, *, minimum_sequence, now=None):
    """Bind a signed sequence once, under the same SQLite writer transaction.

    A matching retry re-verifies current publisher policy and expiry. Neither
    successful verification nor a receipt grants install/rollback permission.
    """
    try:
        if type(minimum_sequence) is not int or minimum_sequence < 0:
            raise ValueError()
        with store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            old = _head(con)
            floor = max(minimum_sequence, old['sequence'] if old else 0)
            release = verify_release(envelope, keys, minimum_sequence=floor, now=now)
            if old and aware_utc(release.verified_at) < aware_utc(old['verified_at']):
                raise ValueError()
            if old and release.sequence == old['sequence']:
                if release.envelope_sha256 != old['envelope_sha256']:
                    raise ValueError()
                return release
            saved = dict(version=1, sequence=release.sequence, envelope_sha256=release.envelope_sha256,
                         archive_sha256=release.archive_sha256, source_sha256=release.manifest.source_tree_sha256,
                         commit=release.commit, key_id=release.key_id,
                         verified_at=release.verified_at, expires_at=release.expires_at)
            raw = json.dumps(saved, sort_keys=True)
            con.execute('INSERT INTO control_state(key,value) VALUES(?,?)', (PREFIX + str(release.sequence), raw))
            con.execute('INSERT INTO control_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                        (HEAD, raw))
        return release
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ReleaseRejected('Chief release history or publisher verification failed.') from None


def latest(store):
    """Read-only UI summary. Absent/corrupt records must never imply success."""
    try:
        with store._connect() as con:
            con.execute('BEGIN')
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='control_state'").fetchone():
                return {'status': 'NOT_CHECKED', 'release': None}
            value = _head(con)
        return {'status': 'VERIFICATION_RECORDED' if value else 'NOT_CHECKED', 'release': value,
                'installed': False, 'activation_authorized': False}
    except (ValueError, TypeError, KeyError, RecursionError):
        return {'status': 'UNAVAILABLE', 'release': None, 'installed': False, 'activation_authorized': False}
