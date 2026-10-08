"""Local release trust administration, separate from operational application data.

Public keys must arrive through an independently reviewed operator channel.
This protects against untrusted release inputs, not compromise of the OS account
or administrator. Windows ACL privacy must be established by the installer.
There is no key generation, network enrollment, service activation or migration.
"""
import base64
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

from compatibility.manifest import _unique_object
from operations.draft_files import _windows_directory
from operations.time_integrity import utc_now, utc_text
from update_center.trust import verify_signed_release

NAME = 'release-trust.sqlite3'
ZERO = '0' * 64
LIMIT = 10000


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 400:
        raise ValueError('An explicit bounded operator reference and reason are required.')
    return value.strip()


@contextmanager
def _connection(root, *, create=False, private_storage_confirmed=False):
    if private_storage_confirmed is not True:
        raise ValueError('Confirm independently provisioned private policy storage.')
    root = Path(root)
    if not root.is_absolute():
        raise ValueError('Policy root must be absolute.')
    with ExitStack() as stack:
        for path in reversed((root, *root.parents)):
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError('Policy paths cannot contain links.')
            if os.name == 'nt':
                stack.enter_context(_windows_directory(path))
        info = root.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Existing private policy directory required.')
        if os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError('Policy directory must be private and owned by this user.')
        path = root / NAME
        if create:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        entry = path.lstat()  # Missing policy is never silently initialized.
        if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1:
            raise ValueError('Policy must be a plain exclusive file.')
        if os.name != 'nt' and (entry.st_uid != os.getuid() or entry.st_mode & 0o077):
            raise ValueError('Policy file must be private.')
        for suffix in ('-journal', '-wal', '-shm'):
            side = Path(str(path) + suffix)
            if side.exists() or side.is_symlink():
                si = side.lstat()
                if not stat.S_ISREG(si.st_mode) or si.st_nlink != 1:
                    raise ValueError('Unsafe policy transaction sidecar.')
        con = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=1)
        try:
            con.execute('PRAGMA trusted_schema=OFF')
            con.execute('PRAGMA synchronous=FULL')
            con.execute('BEGIN IMMEDIATE')
            if create:
                con.execute('CREATE TABLE policy_events (revision INTEGER PRIMARY KEY, document TEXT NOT NULL, digest TEXT NOT NULL)')
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()
        if create and os.name != 'nt':
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(fd)
            finally: os.close(fd)


def _load(con):
    if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        raise ValueError('Trust policy integrity check failed.')
    rows = con.execute('SELECT revision,document,digest FROM policy_events ORDER BY revision LIMIT ?', (LIMIT + 1,)).fetchall()
    if not rows or len(rows) > LIMIT:
        raise ValueError('Trust policy is absent, interrupted or exceeds its supported history limit.')
    previous = ZERO
    prior_state = None
    for revision, text, digest in rows:
        event = json.loads(text, object_pairs_hook=_unique_object)
        if revision != event.get('revision') or revision != (1 if prior_state is None else prior_state['revision'] + 1):
            raise ValueError('Trust history has a revision gap.')
        if event.get('previous') != previous or hashlib.sha256(text.encode()).hexdigest() != digest or text != _json(event):
            raise ValueError('Trust history is inconsistent.')
        state = event['state']
        if set(state) != {'keys', 'revoked', 'minimum_sequence', 'accepted'} or not isinstance(state['keys'], dict):
            raise ValueError('Invalid trust policy state.')
        if type(state['minimum_sequence']) is not int or state['minimum_sequence'] < 0:
            raise ValueError('Invalid sequence floor.')
        for key_id, encoded in state['keys'].items():
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) != 32 or hashlib.sha256(raw).hexdigest() != key_id:
                raise ValueError('Invalid enrolled key.')
        if len(set(state['revoked'])) != len(state['revoked']) or not set(state['revoked']) <= set(state['keys']):
            raise ValueError('Invalid revocations.')
        if prior_state and (state['minimum_sequence'] < prior_state['state']['minimum_sequence'] or not set(prior_state['state']['revoked']) <= set(state['revoked'])):
            raise ValueError('Trust history lowered its sequence floor or undid revocation.')
        previous = digest
        prior_state = event
    return prior_state, previous


def _append(con, old, previous, state, operation, operator, reason):
    revision = 1 if old is None else old['revision'] + 1
    if revision > LIMIT:
        raise ValueError('Trust history capacity reached; explicit maintenance required.')
    event = dict(revision=revision, previous=previous, operation=operation,
                 operator=_text(operator), reason=_text(reason), recorded_at=utc_text(utc_now()), state=state)
    text = _json(event)
    con.execute('INSERT INTO policy_events VALUES (?,?,?)', (revision, text, hashlib.sha256(text.encode()).hexdigest()))
    return {'revision': revision, 'status': 'POLICY_RECORDED', 'activation': 'BLOCKED'}


def initialize(root, *, operator, reason, private_storage_confirmed=False):
    _text(operator); _text(reason)
    with _connection(root, create=True, private_storage_confirmed=private_storage_confirmed) as con:
        return _append(con, None, ZERO, {'keys': {}, 'revoked': [], 'minimum_sequence': 0, 'accepted': {}}, 'INITIALIZE', operator, reason)


def inspect(root, *, private_storage_confirmed=False):
    with _connection(root, private_storage_confirmed=private_storage_confirmed) as con:
        event, digest = _load(con)
        return {'revision': event['revision'], 'history_digest': digest, **event['state'], 'activation': 'BLOCKED'}


def change_key(root, *, operation, public_key, expected_revision, operator, reason, private_storage_confirmed=False):
    if operation not in {'ENROLL', 'REVOKE'} or not isinstance(public_key, bytes) or len(public_key) != 32:
        raise ValueError('Supply an independently reviewed 32-byte public key and ENROLL or REVOKE.')
    key_id = hashlib.sha256(public_key).hexdigest()
    with _connection(root, private_storage_confirmed=private_storage_confirmed) as con:
        event, digest = _load(con)
        if type(expected_revision) is not int or event['revision'] != expected_revision:
            raise ValueError('Trust policy changed; review its current revision.')
        state = event['state']
        if operation == 'ENROLL':
            if key_id in state['keys']:
                raise ValueError('Key already enrolled or revoked; review existing policy.')
            state['keys'][key_id] = base64.b64encode(public_key).decode('ascii')
        else:
            if key_id not in state['keys'] or key_id in state['revoked']:
                raise ValueError('Key is absent or already revoked.')
            state['revoked'].append(key_id)
            state['revoked'].sort()
        return _append(con, event, digest, state, operation, operator, reason)


def review_release(root, document, *, expected_revision, operator, reason, now,
                   expected_archive, expected_source, expected_dependencies, expected_runtime_inventory,
                   private_storage_confirmed=False):
    """Explicit operator acceptance of signed identities; never activation.

    Successful review durably raises the sequence floor. Same-sequence reuse is
    allowed only for the identical signed envelope. Clock quality must be
    independently assessed by the caller; a supplied date is not time attestation.
    """
    with _connection(root, private_storage_confirmed=private_storage_confirmed) as con:
        event, digest = _load(con)
        if type(expected_revision) is not int or event['revision'] != expected_revision:
            raise ValueError('Trust policy changed; review its current revision.')
        state = event['state']
        verified = verify_signed_release(document, trusted_keys={k: base64.b64decode(v) for k,v in state['keys'].items()},
            revoked_keys=set(state['revoked']), minimum_sequence=state['minimum_sequence'], now=now,
            expected_archive=expected_archive, expected_source=expected_source,
            expected_dependencies=expected_dependencies, expected_runtime_inventory=expected_runtime_inventory)
        sequence = str(verified['sequence'])
        envelope = verified['signed_envelope_sha256']
        if sequence in state['accepted'] and state['accepted'][sequence] != envelope:
            raise ValueError('Sequence is already bound to a different release envelope.')
        state['accepted'][sequence] = envelope
        state['minimum_sequence'] = verified['sequence']
        receipt = _append(con, event, digest, state, 'ACCEPT_SIGNED_RELEASE', operator, reason)
        return {**verified, **receipt, 'status': 'RELEASE_REVIEW_RECORDED_NOT_ACTIVATED'}
