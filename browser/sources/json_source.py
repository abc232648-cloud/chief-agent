from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import SourceListing


class JsonJobSource:
    """Deterministic source adapter for local fixtures/exported listings."""

    name = "json"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def fetch(self) -> list[SourceListing]:
        data: Any = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("Job source JSON must contain a list")

        listings: list[SourceListing] = []
        for index, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(f"Job listing {index} must be an object")
            url = str(item.get("url", ""))
            listings.append(SourceListing(self.name, url, dict(item)))
        return listings
