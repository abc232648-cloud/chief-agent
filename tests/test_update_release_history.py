from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import sqlite3

import pytest

from tests.test_update_history import store
from tests.test_update_release_trust import NOW, release
from update_center.release_history import HEAD, PREFIX, latest, verify_and_record
from update_center.release_trust import ReleaseRejected


def test_durable_floor_exact_retry_and_equivocation(store, release):
    key, payload, sign, _ = release
    before = None
    with store._connect() as con:
        before = [tuple(r) for r in con.execute('SELECT sql FROM sqlite_master ORDER BY name')]
        con.execute('INSERT INTO control_state VALUES(?,?)', ('existing', 'preserve'))
    first = verify_and_record(store, sign(payload), [key], minimum_sequence=1, now=NOW)
    assert verify_and_record(store, sign(payload), [key], minimum_sequence=0, now=NOW) == first
    assert latest(store)['release']['sequence'] == 2
    for altered in ({**payload, 'sequence': 1}, {**payload, 'commit': 'b' * 40}):
        with pytest.raises(ReleaseRejected):
            verify_and_record(store, sign(altered), [key], minimum_sequence=0, now=NOW)
    verify_and_record(store, sign({**payload, 'sequence': 3}), [key], minimum_sequence=0, now=NOW)
    with pytest.raises(ReleaseRejected):
        verify_and_record(store, sign(payload), [key], minimum_sequence=0, now=NOW)
    with store._connect() as con:
        assert [tuple(r) for r in con.execute('SELECT sql FROM sqlite_master ORDER BY name')] == before
        assert con.execute('SELECT value FROM control_state WHERE key=?', ('existing',)).fetchone()[0] == 'preserve'
        assert con.execute('SELECT count(*) FROM control_state').fetchone()[0] == 4
    assert latest(store)['activation_authorized'] is False


def test_retry_rechecks_revocation_and_expiry(store, release):
    key, payload, sign, _ = release
    envelope = sign(payload)
    verify_and_record(store, envelope, [key], minimum_sequence=0, now=NOW)
    for keys, when in (([replace(key, revoked=True)], NOW), ([key], payload['expires_at'])):
        with pytest.raises(ReleaseRejected):
            verify_and_record(store, envelope, keys, minimum_sequence=0, now=when)
    with pytest.raises(ReleaseRejected):
        verify_and_record(store, envelope, [key], minimum_sequence=0, now='2026-10-13T11:59:59Z')


def test_concurrent_same_release_is_one_receipt(store, release):
    key, payload, sign, _ = release
    envelope = sign(payload)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: verify_and_record(store, envelope, [key], minimum_sequence=0, now=NOW), range(8)))
    assert all(r == results[0] for r in results)
    with store._connect() as con:
        assert con.execute('SELECT count(*) FROM control_state').fetchone()[0] == 2


def test_concurrent_conflicting_release_cannot_change_sequence_binding(store, release):
    key, payload, sign, _ = release
    def attempt(commit):
        try:
            return verify_and_record(store, sign({**payload, 'commit': commit}), [key], minimum_sequence=0, now=NOW)
        except ReleaseRejected:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ['a' * 40, 'b' * 40]))
    winner, = [r for r in results if r is not None]
    assert latest(store)['release']['commit'] == winner.commit


@pytest.mark.parametrize('damage', ['bad-head', 'missing-head', 'missing-receipt', 'bad-receipt'])
def test_corrupt_or_partial_history_fails_closed(store, release, damage):
    key, payload, sign, _ = release
    envelope = sign(payload)
    verify_and_record(store, envelope, [key], minimum_sequence=0, now=NOW)
    target = HEAD if damage.endswith('head') else PREFIX + '2'
    with store._connect() as con:
        if damage.startswith('missing'):
            con.execute('DELETE FROM control_state WHERE key=?', (target,))
        else:
            con.execute('UPDATE control_state SET value=? WHERE key=?', ('private-sentinel', target))
    result = latest(store)
    assert result['status'] == 'UNAVAILABLE' and 'private-sentinel' not in str(result)
    with pytest.raises(ReleaseRejected):
        verify_and_record(store, envelope, [key], minimum_sequence=0, now=NOW)


def test_interrupted_transaction_does_not_advance_floor(store, release):
    key, payload, sign, _ = release
    # Force failure on the second write; first receipt must roll back too.
    with store._connect() as con:
        con.execute("CREATE TRIGGER fail_head BEFORE INSERT ON control_state WHEN NEW.key='update_release_v1:head' BEGIN SELECT RAISE(ABORT, 'synthetic interrupted write'); END")
    with pytest.raises(sqlite3.IntegrityError):
        verify_and_record(store, sign(payload), [key], minimum_sequence=0, now=NOW)
    assert latest(store)['status'] == 'NOT_CHECKED'
    with store._connect() as con:
        assert con.execute('SELECT count(*) FROM control_state').fetchone()[0] == 0
        con.execute('DROP TRIGGER fail_head')
    assert verify_and_record(store, sign(payload), [key], minimum_sequence=0, now=NOW).sequence == 2


def test_read_without_schema_does_not_create_it(tmp_path):
    from database.store import Store
    empty = Store(tmp_path / 'test.db')
    assert latest(empty)['status'] == 'NOT_CHECKED'
    with empty._connect() as con:
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name='control_state'").fetchone() is None
