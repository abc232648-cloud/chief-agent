"""Farm request scope and record lineage.

Legacy installations with no Chief ownership catalog remain unscoped. Once at
least one Farm identity exists, callers must resolve an authorized Farm through
Chief tenancy and every converted Farm record is stamped with that immutable
organization/farm lineage.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import json

from domains import ownership, tenancy


@dataclass(frozen=True)
class FarmScope:
    legacy: bool
    organization_id: str | None = None
    farm_id: str | None = None
    tenant_role: str | None = None


_CURRENT: ContextVar[FarmScope | None] = ContextVar("chief_farm_scope", default=None)


def farm_count_connection(con) -> int:
    _, farms = ownership._replay(con)
    return len(farms)


def farm_count(store) -> int:
    with store._connect() as con:
        return farm_count_connection(con)


def tenant_mode(store) -> bool:
    return farm_count(store) > 0


def tenant_mode_connection(con) -> bool:
    return farm_count_connection(con) > 0


def current(*, required=False) -> FarmScope | None:
    value = _CURRENT.get()
    if required and value is None:
        raise PermissionError("A Farm scope is required for this operation.")
    return value


@contextmanager
def bind(store, principal, farm_id=None):
    parent = current()
    if parent is not None and not parent.legacy:
        if farm_id is not None and farm_id != parent.farm_id:
            raise PermissionError("A nested Farm operation cannot change its scope.")
        farm_id = parent.farm_id
    if not tenant_mode(store):
        selected = FarmScope(legacy=True)
    else:
        resolved = tenancy.resolve_farm(store, principal, farm_id)
        selected = FarmScope(
            legacy=False,
            organization_id=resolved["organization_id"],
            farm_id=resolved["farm_id"],
            tenant_role=resolved["tenant_role"],
        )
    token = _CURRENT.set(selected)
    try:
        yield selected
    finally:
        _CURRENT.reset(token)


def entry(function):
    """Resolve caller authority for a public Farm operation, including nested calls."""
    @wraps(function)
    def wrapped(store, principal, *args, farm_id=None, **kwargs):
        with bind(store, principal, farm_id):
            return function(store, principal, *args, **kwargs)
    return wrapped


def predicate(con, column="data_json"):
    """SQL scope predicate for internal constant column names, before LIMIT/count."""
    if column not in {"data_json", "d.data_json", "c.data_json"}:
        raise ValueError("Unsupported Farm record column.")
    selected = require_for_connection(con)
    if selected.legacy:
        return f"json_extract({column},'$.scope') IS NULL", ()
    return (f"json_extract({column},'$.scope.organization_id')=? AND "
            f"json_extract({column},'$.scope.farm_id')=?",
            (selected.organization_id, selected.farm_id))


def records(con, kind, *, limit=None, metadata_only=False):
    """Read one Farm's records; other Farms never consume its limit."""
    where, params = predicate(con)
    projection = "json_remove(data_json,'$.image_base64')" if metadata_only else "data_json"
    sql = f"SELECT {projection} FROM domain_records WHERE domain='farming' AND kind=? AND {where} ORDER BY id"
    args = (kind, *params)
    if limit is not None:
        if type(limit) is not int or limit < 1:
            raise ValueError("Invalid record limit.")
        sql += ' LIMIT ?'
        args += (limit,)
    return [json.loads(row[0]) for row in con.execute(sql, args)]


def require_for_connection(con) -> FarmScope:
    selected = current()
    if not tenant_mode_connection(con):
        return selected or FarmScope(legacy=True)
    if selected is None or selected.legacy or not selected.organization_id or not selected.farm_id:
        raise PermissionError("Select an authorized Farm before accessing Farm records.")
    return selected


def lineage() -> dict | None:
    selected = current()
    if selected is None or selected.legacy:
        return None
    return {"organization_id": selected.organization_id, "farm_id": selected.farm_id}


def stamp(record: dict) -> dict:
    selected = current()
    if selected is None or selected.legacy:
        return record
    if "scope" in record:
        raise ValueError("Farm record scope is supplied by Chief, not the client.")
    return {**record, "scope": lineage()}


def matches(record: dict, selected: FarmScope) -> bool:
    value = record.get("scope")
    if selected.legacy:
        return value is None
    return value == {"organization_id": selected.organization_id, "farm_id": selected.farm_id}


def filter_records(con, records):
    selected = require_for_connection(con)
    return [record for record in records if matches(record, selected)]


def role_for_connection(con, principal):
    if not tenant_mode_connection(con):
        return None
    selected = require_for_connection(con)
    # Recheck assignment inside the caller's transaction: a cached request scope
    # must not retain a revoked role during an AI request or multi-step operation.
    if principal.role in {"Owner", "Administrator"} and '*' in principal.domains:
        return "OWNER"
    assigned = [row for row in tenancy._replay(con).values()
                if row['active'] and row['human_id'] == principal.id
                and row['farm_id'] == selected.farm_id
                and row['organization_id'] == selected.organization_id]
    if len(assigned) != 1:
        raise PermissionError("Farm access was revoked or needs review.")
    return assigned[0]['role']


def member(con, row):
    """Filter identity directories without exposing unrelated Farm accounts."""
    if not tenant_mode_connection(con):
        return True
    selected = require_for_connection(con)
    if row['role'] in {'Owner', 'Administrator'} and '*' in json.loads(row['domains']):
        return True
    return any(item['active'] and item['human_id'] == row['id']
               and item['farm_id'] == selected.farm_id
               and item['organization_id'] == selected.organization_id
               for item in tenancy._replay(con).values())
