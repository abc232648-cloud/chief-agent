"""Versioned, bounded password hashing; legacy verification never writes legacy hashes."""
import hashlib
import hmac
import re
from contextlib import contextmanager
from threading import BoundedSemaphore

from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import VerificationError, InvalidHashError

PREFIX = 'chief-v2:'
HASHER = PasswordHasher(memory_cost=65536, time_cost=3, parallelism=1,
                        hash_len=32, salt_len=16, type=Type.ID)
_SLOTS = BoundedSemaphore(2)
LEGACY = re.compile(r'[0-9a-f]{32}:[0-9a-f]{128}\Z')
DUMMY = PREFIX + '$argon2id$v=19$m=65536,t=3,p=1$MDAwMDAwMDAwMDAwMDAwMA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'


@contextmanager
def _slot():
    if not _SLOTS.acquire(timeout=0.25):
        raise PermissionError('Password verification is busy; try again shortly.')
    try:
        yield
    finally:
        _SLOTS.release()


def _valid_password(password):
    return isinstance(password, str) and 12 <= len(password) <= 1024


def password_hash(password):
    if not _valid_password(password):
        raise ValueError('Use a password of 12–1024 characters.')
    with _slot():
        return PREFIX + HASHER.hash(password)


def check_password(password, stored):
    if not _valid_password(password) or not isinstance(stored, str) or len(stored) > 512:
        return False
    try:
        if LEGACY.fullmatch(stored):
            salt, expected = stored.split(':')
            with _slot():
                actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                    n=16384, r=8, p=1, maxmem=64*1024*1024).hex()
            return hmac.compare_digest(actual, expected)
        if not stored.startswith(PREFIX):
            return False
        encoded = stored[len(PREFIX):]
        parameters = extract_parameters(encoded)
        # Validate before invoking native code: a corrupt/untrusted record cannot
        # request unbounded memory, threads or CPU time.
        if (parameters.type != Type.ID or parameters.version != 19 or
                parameters.hash_len != 32 or parameters.salt_len != 16 or
                (parameters.memory_cost, parameters.time_cost, parameters.parallelism)
                not in {(19456, 2, 1), (65536, 3, 1)}):
            return False
        with _slot():
            return HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError, ValueError, TypeError):
        return False


def needs_upgrade(stored):
    return not stored.startswith(PREFIX) or HASHER.check_needs_rehash(stored[len(PREFIX):])
