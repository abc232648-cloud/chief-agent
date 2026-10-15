"""Pin the detected Farm reporting zone; preserve pre-setting legacy history."""
import json
import time
from zoneinfo import ZoneInfo

from operations.local_timezone import host_zone
from operations.time_integrity import utc_now, utc_text, aware_utc
from . import scope

KIND = 'farm_timezone_v1'


def _validated(rows):
    if len(rows) != 1:
        raise ValueError('Farm reporting timezone needs review; conflicting settings exist.')
    value = {key: item for key, item in rows[0].items() if key != 'scope'}
    if (set(value) != {'version', 'timezone', 'basis', 'recorded_at'} or
        type(value['version']) is not int or value['version'] != 1 or
        value['basis'] not in {'LEGACY_HISTORY', 'HOST_DETECTED'}):
        raise ValueError('Unsupported Farm timezone setting.')
    ZoneInfo(value['timezone'])
    aware_utc(value['recorded_at'])
    return value


def setting(con):
    selected = scope.require_for_connection(con)
    rows = scope.records(con, KIND)
    if rows:
        return _validated(rows)
    if not selected.legacy:
        where, params = scope.predicate(con)
        existing = con.execute(f"SELECT 1 FROM domain_records WHERE domain='farming' AND {where} LIMIT 1", params).fetchone()
        if existing:
            # Earlier deferred builds used one installation setting. Preserve its
            # historical interpretation for existing scoped records, never detect
            # a new host zone and silently move their reporting-day boundaries.
            old = [json.loads(r[0]) for r in con.execute(
                "SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? AND json_extract(data_json,'$.scope') IS NULL", (KIND,))]
            if not old:
                raise ValueError('Existing Farm records need a reviewed reporting timezone.')
            return {**_validated(old), 'recorded_at': None}
    legacy = selected.legacy and con.execute("SELECT 1 FROM domain_records WHERE domain='farming' AND json_extract(data_json,'$.scope') IS NULL LIMIT 1").fetchone()
    # Old releases interpreted these records using Lagos. This is a legacy
    # compatibility rule, never the default for a fresh installation.
    return {'version': 1, 'timezone': 'Africa/Lagos' if legacy else host_zone().key,
            'basis': 'LEGACY_HISTORY' if legacy else 'HOST_DETECTED', 'recorded_at': None}


def zone(store_or_connection):
    if hasattr(store_or_connection, '_connect'):
        with store_or_connection._connect() as con:
            return ZoneInfo(setting(con)['timezone'])
    return ZoneInfo(setting(store_or_connection)['timezone'])


def pin(con):
    """Called inside the authorized first-write transaction, never on a GET."""
    if not con.in_transaction:
        raise ValueError('Timezone setup requires the caller transaction.')
    value = setting(con)
    if value['recorded_at'] is None:
        value = {**value, 'recorded_at': utc_text(utc_now())}
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",
                    (KIND, json.dumps(scope.stamp(value), sort_keys=True), time.time()))
    return value


def unambiguous_deadline(wall_time, report_zone):
    """Do not label a report late against a nonexistent or repeated wall time."""
    instants = set()
    for fold in (0, 1):
        instant = aware_utc(wall_time.replace(tzinfo=report_zone, fold=fold))
        if instant.astimezone(report_zone).replace(tzinfo=None) == wall_time:
            instants.add(instant)
    return next(iter(instants)) if len(instants) == 1 else None
