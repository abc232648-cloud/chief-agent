from __future__ import annotations

from typing import Any


def accept_raw_listing(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate the minimum fields needed before normalization."""
    if not isinstance(raw, dict):
        raise TypeError("Job listing must be a mapping")
    if not str(raw.get("title", "")).strip():
        raise ValueError("Job listing requires a title")
    return dict(raw)
