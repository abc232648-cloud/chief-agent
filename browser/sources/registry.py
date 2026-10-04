from __future__ import annotations

from typing import Iterable

from .base import JobSource, SourceListing


class SourceRegistry:
    """Collects listings from approved read-only job sources."""

    def __init__(self, sources: Iterable[JobSource] = ()):
        self._sources = list(sources)

    def add(self, source: JobSource) -> None:
        self._sources.append(source)

    def fetch_all(self) -> list[SourceListing]:
        listings: list[SourceListing] = []
        for source in self._sources:
            listings.extend(source.fetch())
        return listings
