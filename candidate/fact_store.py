from __future__ import annotations
import json
from typing import Any

CONFIRMED = 'USER_CONFIRMED'
PROPOSED = 'PROPOSED'
REVOKED = 'REVOKED'
VALID_STATUSES = {CONFIRMED, PROPOSED, REVOKED}


def normalize_facts(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    facts = payload.get('facts', [])
    return [f for f in facts if isinstance(f, dict) and str(f.get('text', '')).strip()]


def confirmed_facts(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Return only user-confirmed facts for AI/application use."""
    return {'facts': [f for f in normalize_facts(payload) if f.get('status', f.get('evidence_state')) == CONFIRMED]}


def validate_fact_status(status: str) -> bool:
    return status in VALID_STATUSES
