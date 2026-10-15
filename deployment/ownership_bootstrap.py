"""First-install bootstrap for Chief organization/farm identity.

This is intentionally deployment-only. Normal ownership changes go through the
authenticated ownership API. Fresh installation may seed one organization and,
when Farm operations are enabled, one initial farm before production state is
promoted from the isolated fixture.
"""
from __future__ import annotations

import json
import time
import uuid

from domains import ownership
from operations.time_integrity import utc_now, utc_text


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def bootstrap_initial(store, owner_id: str, organization_name: str, farm_name: str | None = None) -> dict:
    """Seed the first organization/farm into an otherwise empty ownership ledger.

    The caller must provide the installation-wide Owner created during the same
    isolated first-run preparation. No browser/session principal is manufactured
    for installation bootstrap; the append-only record explicitly carries a null
    session and the Owner identity that caused the installation.
    """
    organization_name = ownership._text(organization_name, "Organization name", ownership._MAX_NAME)
    if farm_name is not None:
        farm_name = ownership._text(farm_name, "Farm name", ownership._MAX_NAME)

    organization_id = _new_id("org")
    organization_event = _new_id("bootstrap-org")
    farm_id = _new_id("farm") if farm_name is not None else None
    farm_event = _new_id("bootstrap-farm") if farm_name is not None else None
    received_at = utc_text(utc_now())

    with store._connect() as con:
        con.execute("BEGIN IMMEDIATE")
        if ownership._rows(con):
            raise PermissionError("First-install ownership bootstrap requires an empty ownership ledger.")

        owner = con.execute(
            "SELECT id,role,domains,enabled FROM human_identities WHERE id=?",
            (owner_id,),
        ).fetchone()
        if not owner or not owner["enabled"] or owner["role"] != "Owner" or "*" not in json.loads(owner["domains"]):
            raise PermissionError("First-install ownership bootstrap requires the installation-wide Owner.")

        records = [
            {
                "version": 1,
                "payload": {
                    "event_id": organization_event,
                    "operation": "create_organization",
                    "organization_id": organization_id,
                    "name": organization_name,
                },
                "actor_id": owner_id,
                "session_id": None,
                "received_at": received_at,
            }
        ]
        if farm_name is not None:
            records.append(
                {
                    "version": 1,
                    "payload": {
                        "event_id": farm_event,
                        "operation": "create_farm",
                        "farm_id": farm_id,
                        "organization_id": organization_id,
                        "name": farm_name,
                    },
                    "actor_id": owner_id,
                    "session_id": None,
                    "received_at": received_at,
                }
            )

        for record in records:
            con.execute(
                "INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)",
                (ownership.DOMAIN, ownership.KIND, json.dumps(record, sort_keys=True), time.time()),
            )

        # Replay immediately so malformed bootstrap history can never be promoted.
        organizations, farms = ownership._replay(con)
        if organization_id not in organizations or (farm_id is not None and farm_id not in farms):
            raise ValueError("First-install ownership bootstrap did not replay cleanly.")

        con.execute(
            "INSERT INTO human_security_events(human_id,session_id,operation,domain,resource,outcome,authority,received_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (owner_id, None, "CHIEF_OWNERSHIP_BOOTSTRAP", None, organization_id, "RECORDED", "INSTALLER", received_at),
        )

    return {
        "organization_id": organization_id,
        "organization_name": organization_name,
        "farm_id": farm_id,
        "farm_name": farm_name,
    }
