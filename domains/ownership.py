"""Chief-wide organization and farm identities on the existing append-only ledger.

This is deliberately separate from Farm operational entities such as houses and
flocks. One Chief may own multiple organizations, and each organization may own
multiple farms. Stable IDs survive display-name changes.
"""
from __future__ import annotations

import json
import re
import time

from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text

DOMAIN = "chief"
KIND = "chief_ownership_v1"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_NAME = 120
_MAX_REASON = 400


def _identifier(value, label):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{label} must be a stable identifier.")
    return value


def _text(value, label, limit):
    if not isinstance(value, str) or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{label} contains unsupported characters.")
    value = " ".join(value.split())
    if not value or len(value) > limit:
        raise ValueError(f"{label} must contain 1-{limit} characters.")
    return value


def _rows(con):
    result = []
    for row in con.execute(
        "SELECT data_json FROM domain_records WHERE domain=? AND kind=? ORDER BY id",
        (DOMAIN, KIND),
    ):
        record = json.loads(row[0])
        if not isinstance(record, dict) or record.get("version") != 1 or not isinstance(record.get("payload"), dict):
            raise ValueError("Chief ownership history is malformed.")
        result.append(record)
    return result


def _replay(con):
    organizations = {}
    farms = {}
    event_ids = set()
    for record in _rows(con):
        payload = record["payload"]
        event_id = _identifier(payload.get("event_id"), "Event id")
        if event_id in event_ids:
            raise ValueError("Chief ownership history contains a duplicate event id.")
        event_ids.add(event_id)
        operation = payload.get("operation")
        if operation == "create_organization":
            organization_id = _identifier(payload.get("organization_id"), "Organization id")
            if organization_id in organizations:
                raise ValueError("Chief ownership history recreates an organization id.")
            organizations[organization_id] = {
                "organization_id": organization_id,
                "name": _text(payload.get("name"), "Organization name", _MAX_NAME),
                "revision": event_id,
            }
        elif operation == "rename_organization":
            organization_id = _identifier(payload.get("organization_id"), "Organization id")
            current = organizations.get(organization_id)
            if current is None or payload.get("expected_revision") != current["revision"]:
                raise ValueError("Chief ownership history contains an invalid organization revision.")
            current.update(
                name=_text(payload.get("name"), "Organization name", _MAX_NAME),
                revision=event_id,
            )
        elif operation == "create_farm":
            farm_id = _identifier(payload.get("farm_id"), "Farm id")
            organization_id = _identifier(payload.get("organization_id"), "Organization id")
            if organization_id not in organizations:
                raise ValueError("Chief ownership history contains a farm without its organization.")
            if farm_id in farms:
                raise ValueError("Chief ownership history recreates a farm id.")
            farms[farm_id] = {
                "farm_id": farm_id,
                "organization_id": organization_id,
                "name": _text(payload.get("name"), "Farm name", _MAX_NAME),
                "revision": event_id,
            }
        elif operation == "rename_farm":
            farm_id = _identifier(payload.get("farm_id"), "Farm id")
            current = farms.get(farm_id)
            if current is None or payload.get("expected_revision") != current["revision"]:
                raise ValueError("Chief ownership history contains an invalid farm revision.")
            current.update(name=_text(payload.get("name"), "Farm name", _MAX_NAME), revision=event_id)
        else:
            raise ValueError("Chief ownership history contains an unknown operation.")
    return organizations, farms


def _authorize(store, principal, *, write=False):
    service = IdentityService(store)
    principal = service.refresh(principal)
    service.authorize(principal, "installation.manage", sensitive=write)
    return service, principal


def overview(store, principal):
    _authorize(store, principal)
    with store._connect() as con:
        organizations, farms = _replay(con)
    grouped = []
    for organization in sorted(organizations.values(), key=lambda item: (item["name"].casefold(), item["organization_id"])):
        children = [farm.copy() for farm in farms.values() if farm["organization_id"] == organization["organization_id"]]
        children.sort(key=lambda item: (item["name"].casefold(), item["farm_id"]))
        grouped.append({**organization, "farms": children})
    return {
        "organizations": grouped,
        "organization_count": len(organizations),
        "farm_count": len(farms),
    }


def append(store, principal, payload):
    if not isinstance(payload, dict):
        raise ValueError("Chief ownership record is required.")
    service, principal = _authorize(store, principal, write=True)
    p = dict(payload)
    operation = p.get("operation")
    common = {"event_id", "operation"}

    if operation == "create_organization":
        if set(p) != common | {"organization_id", "name"}:
            raise ValueError("Supply exactly the organization creation fields.")
        _identifier(p.get("organization_id"), "Organization id")
        p["name"] = _text(p.get("name"), "Organization name", _MAX_NAME)
    elif operation == "rename_organization":
        if set(p) != common | {"organization_id", "expected_revision", "name", "reason"}:
            raise ValueError("Supply exactly the organization rename fields.")
        _identifier(p.get("organization_id"), "Organization id")
        _identifier(p.get("expected_revision"), "Expected revision")
        p["name"] = _text(p.get("name"), "Organization name", _MAX_NAME)
        p["reason"] = _text(p.get("reason"), "Rename reason", _MAX_REASON)
    elif operation == "create_farm":
        if set(p) != common | {"farm_id", "organization_id", "name"}:
            raise ValueError("Supply exactly the farm creation fields.")
        _identifier(p.get("farm_id"), "Farm id")
        _identifier(p.get("organization_id"), "Organization id")
        p["name"] = _text(p.get("name"), "Farm name", _MAX_NAME)
    elif operation == "rename_farm":
        if set(p) != common | {"farm_id", "expected_revision", "name", "reason"}:
            raise ValueError("Supply exactly the farm rename fields.")
        _identifier(p.get("farm_id"), "Farm id")
        _identifier(p.get("expected_revision"), "Expected revision")
        p["name"] = _text(p.get("name"), "Farm name", _MAX_NAME)
        p["reason"] = _text(p.get("reason"), "Rename reason", _MAX_REASON)
    else:
        raise ValueError("Unknown Chief ownership operation.")
    _identifier(p.get("event_id"), "Event id")

    with store._connect() as con:
        con.execute("BEGIN IMMEDIATE")
        organizations, farms = _replay(con)
        prior = next((record for record in _rows(con) if record["payload"].get("event_id") == p["event_id"]), None)
        if prior:
            if prior["payload"] != p or prior.get("actor_id") != principal.id:
                raise ValueError("Submission identifier conflict.")
            return {"status": "ALREADY_RECORDED", "record": prior}

        if operation == "create_organization":
            if p["organization_id"] in organizations:
                raise ValueError("Organization identifier already exists.")
            if any(item["name"].casefold() == p["name"].casefold() for item in organizations.values()):
                raise ValueError("An organization with that name already exists in this Chief.")
        elif operation == "rename_organization":
            current = organizations.get(p["organization_id"])
            if not current or current["revision"] != p["expected_revision"]:
                raise ValueError("Organization changed or is missing; refresh before saving.")
            if any(
                key != p["organization_id"] and item["name"].casefold() == p["name"].casefold()
                for key, item in organizations.items()
            ):
                raise ValueError("An organization with that name already exists in this Chief.")
        elif operation == "create_farm":
            if p["organization_id"] not in organizations:
                raise ValueError("Choose an existing organization for this farm.")
            if p["farm_id"] in farms:
                raise ValueError("Farm identifier already exists.")
            if any(
                item["organization_id"] == p["organization_id"] and item["name"].casefold() == p["name"].casefold()
                for item in farms.values()
            ):
                raise ValueError("A farm with that name already exists in this organization.")
        else:
            current = farms.get(p["farm_id"])
            if not current or current["revision"] != p["expected_revision"]:
                raise ValueError("Farm changed or is missing; refresh before saving.")
            if any(
                key != p["farm_id"]
                and item["organization_id"] == current["organization_id"]
                and item["name"].casefold() == p["name"].casefold()
                for key, item in farms.items()
            ):
                raise ValueError("A farm with that name already exists in this organization.")

        record = {
            "version": 1,
            "payload": p,
            "actor_id": principal.id,
            "session_id": principal.session_id,
            "received_at": utc_text(utc_now()),
        }
        con.execute(
            "INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)",
            (DOMAIN, KIND, json.dumps(record, sort_keys=True), time.time()),
        )
        service._event(con, principal, "CHIEF_OWNERSHIP_RECORDED", None, p["event_id"], "RECORDED")
    return {"status": "RECORDED", "record": record}
