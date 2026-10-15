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
    if selected.tenant_role in {"OWNER", "ADMINISTRATOR"}:
        return "OWNER"
    return selected.tenant_role
