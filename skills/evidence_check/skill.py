from __future__ import annotations

from policy.rules import evidence_is_true, valid_evidence_state


def can_claim(state: str) -> bool:
    """Return whether evidence can be used as a factual candidate claim."""
    if not valid_evidence_state(state):
        return False
    return evidence_is_true(state)
