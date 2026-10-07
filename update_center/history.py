"""Append-only local operator observations; never authorize update activation."""
import json
import re
from operations.time_integrity import utc_now, utc_text

PREFIX = 'update_history_v1:'
FIELDS = {'event_id', 'outcome', 'source_sha256', 'evidence_sha256'}
OUTCOMES = {'STAGED', 'DEPLOYED', 'ROLLED_BACK', 'FAILED'}


def validate(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('Supply only the update event identity, outcome and source/evidence digests.')
    if not isinstance(value['event_id'], str) or not re.fullmatch(r'[a-f0-9]{32}', value['event_id']):
        raise ValueError('Event identity must be 32 lowercase hexadecimal characters.')
    if value['outcome'] not in OUTCOMES:
        raise ValueError('Unknown update outcome.')
    for field in ('source_sha256', 'evidence_sha256'):
        if not isinstance(value[field], str) or not re.fullmatch(r'[a-f0-9]{64}', value[field]):
            raise ValueError('Source and evidence require SHA256 digests.')


def record(store, event):
    """Trusted local operator API, with no remote mutation route or schema DDL."""
    validate(event)
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        key = PREFIX + event['event_id']
        old = con.execute('SELECT value FROM control_state WHERE key=?', (key,)).fetchone()
        if old:
            saved = json.loads(old[0])
            if {k: saved[k] for k in FIELDS} != event:
                raise ValueError('An existing event cannot be changed.')
            return saved
        saved = dict(event, recorded_at=utc_text(utc_now()), version=1)
        con.execute('INSERT INTO control_state(key,value) VALUES(?,?)', (key, json.dumps(saved, sort_keys=True)))
    return saved


def read(store, *, offset=0, limit=20):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Invalid history page.')
    with store._connect() as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='control_state'").fetchone():
            return {'events': [], 'invalid_records': 0, 'has_more': False}
        # Existing storage uses operator-controlled keys. JSON failures are displayed
        # as unavailable records, never as a successful update.
        rows = con.execute('SELECT key,value FROM control_state WHERE substr(key,1,?)=? ORDER BY rowid DESC LIMIT ? OFFSET ?',
                           (len(PREFIX), PREFIX, limit + 1, offset)).fetchall()
    events, invalid = [], 0
    for row in rows[:limit]:
        try:
            value = json.loads(row['value'])
            validate({k: value[k] for k in FIELDS})
            if set(value) != FIELDS | {'recorded_at', 'version'} or value['version'] != 1 or row['key'] != PREFIX + value['event_id']:
                raise ValueError()
            from operations.time_integrity import aware_utc
            aware_utc(value['recorded_at'])
            events.append(value)
        except (ValueError, KeyError, TypeError):
            invalid += 1
    return {'events': events, 'invalid_records': invalid, 'has_more': len(rows) > limit}
