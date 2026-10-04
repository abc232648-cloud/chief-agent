from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class SourceListing:
    """Raw listing plus provenance. No source content is trusted as candidate facts."""

    source: str
    url: str
    payload: dict[str, Any]

    def as_raw_job(self) -> dict[str, Any]:
        result = dict(self.payload)
        result.setdefault("source", self.source)
        result.setdefault("url", self.url)
        return result


class JobSource(Protocol):
    name: str

    def fetch(self) -> list[SourceListing]:
        """Return raw listings. Implementations must not execute application actions."""
        ...
