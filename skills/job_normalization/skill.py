from __future__ import annotations

from typing import Any


def normalize_job(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw listing into a stable, conservative job record."""
    title = str(raw.get("title", "")).strip()
    company = str(raw.get("company", "")).strip()
    description = str(raw.get("description", "")).strip()
    location = str(raw.get("location", "")).strip()
    remote = bool(raw.get("remote", False))
    compensation = raw.get("compensation")
    source = str(raw.get("source", "")).strip()
    url = str(raw.get("url", "")).strip()
    return {
        "title": title,
        "company": company,
        "description": description,
        "location": location,
        "remote": remote,
        "compensation": compensation,
        "source": source,
        "url": url,
    }
