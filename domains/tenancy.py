"""Chief organization/farm access assignments.

Ownership identifies organizations and farms. Tenancy identifies which ordinary
human identities may enter which farm scopes, with a farm-local role. Installation
Owners/Administrators remain global authorities and are never represented by
ordinary tenant assignments.
"""
from __future__ import annotations

import json
import time

from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text
from . import ownership

DOMAIN = "chief"
KIND = "chief_tenancy_v1"
ROLES = ("GENERAL_MANAGER", "SUPERVISOR", "WORKER")


def _rows(con):
    result = []
    for row in con.execute(
        "SELECT data_json FROM domain_records WHERE domain=? AND kind=? ORDER BY id",
        (DOMAIN, KIND),
    ):
        record = json.loads(row[0])
        if not isinstance(record, dict) or record.get("version") != 1 or not isinstance(record.get("payload"), dict):
            raise ValueError("Chief tenancy history is malformed.")
        result.append(record)
    return result


def _role(value):
    if value not in ROLES:
        raise ValueError("Choose a known Farm tenant role.")
    return value


def _reason(value):
    return ownership._text(value, "Assignment reason", ownership._MAX_REASON)


def _replay(con):
    assignments = {}
    event_ids = set()
    for record in _rows(con):
        payload = record["payload"]
        event_id = ownership._identifier(payload.get("event_id"), "Event id")
        if event_id in event_ids:
            raise ValueError("Chief tenancy history contains a duplicate event id.")
        event_ids.add(event_id)
        operation = payload.get("operation")
        if operation == "assign_user_farm":
            assignment_id = ownership._identifier(payload.get("assignment_id"), "Assignment id")
            if assignment_id in assignments:
                raise ValueError("Chief tenancy history recreates an assignment id.")
            assignments[assignment_id] = {
                "assignment_id": assignment_id,
                "human_id": ownership._identifier(payload.get("human_id"), "Human id"),
                "organization_id": ownership._identifier(payload.get("organization_id"), "Organization id"),
                "farm_id": ownership._identifier(payload.get("farm_id"), "Farm id"),
                "role": _role(payload.get("role")),
                "active": True,
                "revision": event_id,
            }
        elif operation == "change_user_farm_role":
            assignment_id = ownership._identifier(payload.get("assignment_id"), "Assignment id")
            current = assignments.get(assignment_id)
            if current is None or not current["active"] or payload.get("expected_revision") != current["revision"]:
                raise ValueError("Chief tenancy history contains an invalid assignment revision.")
            current.update(role=_role(payload.get("role")), revision=event_id)
        elif operation == "revoke_user_farm":
            assignment_id = ownership._identifier(payload.get("assignment_id"), "Assignment id")
            current = assignments.get(assignment_id)
            if current is None or not current["active"] or payload.get("expected_revision") != current["revision"]:
                raise ValueError("Chief tenancy history contains an invalid assignment revocation.")
            current.update(active=False, revision=event_id)
        else:
            raise ValueError("Chief tenancy history contains an unknown operation.")
    return assignments


def _global(principal):
    return principal.role in {"Owner", "Administrator"} and "*" in principal.domains


def _authorize_admin(store, principal):
    service = IdentityService(store)
    principal = service.refresh(principal)
    service.authorize(principal, "installation.manage", sensitive=True)
    if not _global(principal):
        raise PermissionError("Tenant assignments require installation-wide authority.")
    return service, principal


def _target(con, human_id, role):
    row = con.execute(
        "SELECT id,role,domains,enabled FROM human_identities WHERE id=?",
        (human_id,),
    ).fetchone()
    if not row or not row["enabled"]:
        raise PermissionError("Choose an enabled Chief identity.")
    domains = set(json.loads(row["domains"]))
    if not ({"farming", "*"} & domains):
        raise PermissionError("The identity must have Farm domain access.")
    if row["role"] in {"Owner", "Administrator"}:
        raise PermissionError("Installation authorities are global and cannot receive tenant assignments.")
    if role == "GENERAL_MANAGER" and row["role"] != "Manager":
        raise ValueError("A General Manager assignment requires a Manager-level identity.")
    return row


def append(store, principal, payload):
    if not isinstance(payload, dict):
        raise ValueError("Chief tenancy record is required.")
    service, principal = _authorize_admin(store, principal)
    p = dict(payload)
    operation = p.get("operation")
    common = {"event_id", "operation"}

    if operation == "assign_user_farm":
        expected = common | {"assignment_id", "human_id", "organization_id", "farm_id", "role", "reason"}
        if set(p) != expected:
            raise ValueError("Supply exactly the Farm assignment fields.")
        for key, label in (("assignment_id", "Assignment id"), ("human_id", "Human id"),
                           ("organization_id", "Organization id"), ("farm_id", "Farm id")):
            ownership._identifier(p.get(key), label)
        p["role"] = _role(p.get("role"))
        p["reason"] = _reason(p.get("reason"))
    elif operation == "change_user_farm_role":
        expected = common | {"assignment_id", "expected_revision", "role", "reason"}
        if set(p) != expected:
            raise ValueError("Supply exactly the Farm role-change fields.")
        ownership._identifier(p.get("assignment_id"), "Assignment id")
        ownership._identifier(p.get("expected_revision"), "Expected revision")
        p["role"] = _role(p.get("role"))
        p["reason"] = _reason(p.get("reason"))
    elif operation == "revoke_user_farm":
        expected = common | {"assignment_id", "expected_revision", "reason"}
        if set(p) != expected:
            raise ValueError("Supply exactly the Farm assignment revocation fields.")
        ownership._identifier(p.get("assignment_id"), "Assignment id")
        ownership._identifier(p.get("expected_revision"), "Expected revision")
        p["reason"] = _reason(p.get("reason"))
    else:
        raise ValueError("Unknown Chief tenancy operation.")
    ownership._identifier(p.get("event_id"), "Event id")

    with store._connect() as con:
        con.execute("BEGIN IMMEDIATE")
        organizations, farms = ownership._replay(con)
        assignments = _replay(con)
        prior = next((record for record in _rows(con) if record["payload"].get("event_id") == p["event_id"]), None)
        if prior:
            if prior["payload"] != p or prior.get("actor_id") != principal.id:
                raise ValueError("Submission identifier conflict.")
            return {"status": "ALREADY_RECORDED", "record": prior}

        if operation == "assign_user_farm":
            farm = farms.get(p["farm_id"])
            if p["organization_id"] not in organizations or not farm or farm["organization_id"] != p["organization_id"]:
                raise ValueError("Choose a Farm that belongs to the selected organization.")
            _target(con, p["human_id"], p["role"])
            if p["assignment_id"] in assignments:
                raise ValueError("Assignment identifier already exists.")
            if any(item["active"] and item["human_id"] == p["human_id"] and item["farm_id"] == p["farm_id"]
                   for item in assignments.values()):
                raise ValueError("That identity is already assigned to this Farm.")
        else:
            current = assignments.get(p["assignment_id"])
            if not current or not current["active"] or current["revision"] != p["expected_revision"]:
                raise ValueError("Farm assignment changed or is missing; refresh before saving.")
            if operation == "change_user_farm_role":
                _target(con, current["human_id"], p["role"])

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
        service._event(con, principal, "CHIEF_TENANCY_RECORDED", None, p["event_id"], "RECORDED")
    return {"status": "RECORDED", "record": record}


def _catalog(con):
    organizations, farms = ownership._replay(con)
    return organizations, farms, _replay(con)


def visible_farms(store, principal):
    service = IdentityService(store)
    principal = service.refresh(principal)
    service.authorize(principal, "work.read", "farming", sensitive=False)
    with store._connect() as con:
        organizations, farms, assignments = _catalog(con)
        if _global(principal):
            visible = [
                {
                    "organization_id": farm["organization_id"],
                    "organization_name": organizations[farm["organization_id"]]["name"],
                    "farm_id": farm["farm_id"],
                    "farm_name": farm["name"],
                    "tenant_role": "OWNER" if principal.role == "Owner" else "ADMINISTRATOR",
                }
                for farm in farms.values()
            ]
        else:
            visible = []
            for assignment in assignments.values():
                if not assignment["active"] or assignment["human_id"] != principal.id:
                    continue
                farm = farms.get(assignment["farm_id"])
                organization = organizations.get(assignment["organization_id"])
                if not farm or not organization or farm["organization_id"] != organization["organization_id"]:
                    raise ValueError("Chief tenancy references an invalid Farm ownership scope.")
                visible.append({
                    "organization_id": organization["organization_id"],
                    "organization_name": organization["name"],
                    "farm_id": farm["farm_id"],
                    "farm_name": farm["name"],
                    "tenant_role": assignment["role"],
                    "assignment_id": assignment["assignment_id"],
                    "revision": assignment["revision"],
                })
    visible.sort(key=lambda item: (item["organization_name"].casefold(), item["farm_name"].casefold(), item["farm_id"]))
    return visible


def resolve_farm(store, principal, farm_id=None):
    visible = visible_farms(store, principal)
    if farm_id is None:
        if len(visible) == 1:
            return visible[0]
        if not visible:
            raise PermissionError("This identity is not assigned to a Farm.")
        raise ValueError("Select a Farm before using Farm operations.")
    ownership._identifier(farm_id, "Farm id")
    match = next((item for item in visible if item["farm_id"] == farm_id), None)
    if match is None:
        raise PermissionError("This identity is not authorized for the selected Farm.")
    return match


def require_farm(store, principal, farm_id=None, *, action="read"):
    scope = resolve_farm(store, principal, farm_id)
    role = scope["tenant_role"]
    if role in {"OWNER", "ADMINISTRATOR"}:
        return scope
    allowed = {
        "read": {"GENERAL_MANAGER", "SUPERVISOR", "WORKER"},
        "request": {"GENERAL_MANAGER", "SUPERVISOR", "WORKER"},
        "manage": {"GENERAL_MANAGER", "SUPERVISOR"},
        "setup": {"GENERAL_MANAGER"},
        "finance": {"GENERAL_MANAGER"},
    }
    if action not in allowed:
        raise ValueError("Unknown Farm tenancy action.")
    if role not in allowed[action]:
        raise PermissionError("The Farm assignment does not grant this operation.")
    return scope


def overview(store, principal):
    service = IdentityService(store)
    principal = service.refresh(principal)
    if not _global(principal):
        farms = visible_farms(store, principal)
        return {"global": False, "farms": farms, "farm_count": len(farms)}
    service.authorize(principal, "installation.manage", sensitive=False)
    with store._connect() as con:
        organizations, farms, assignments = _catalog(con)
        active = [dict(item) for item in assignments.values() if item["active"]]
        active.sort(key=lambda item: (item["organization_id"], item["farm_id"], item["human_id"]))
        grouped = []
        for organization in sorted(organizations.values(), key=lambda item: (item["name"].casefold(), item["organization_id"])):
            children = [dict(farm) for farm in farms.values() if farm["organization_id"] == organization["organization_id"]]
            children.sort(key=lambda item: (item["name"].casefold(), item["farm_id"]))
            grouped.append({**organization, "farms": children})
    return {"global": True, "organizations": grouped, "assignments": active,
            "organization_count": len(organizations), "farm_count": len(farms), "assignment_count": len(active)}
