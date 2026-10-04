from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from config.source_config import ApprovedSource, SourceConfigError
from .base import JobSource, SourceListing


class ControlledSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ControlledUrl:
    source: ApprovedSource
    url: str


class ControlledPlaywrightJobSource:
    """Read approved URLs only; never performs application actions."""

    def __init__(self, source: ApprovedSource, urls: tuple[str, ...], timeout_ms: int = 20_000):
        if not source.enabled:
            raise ControlledSourceError(f"Source {source.name} is disabled")
        if not source.read_only:
            raise SourceConfigError(f"Source {source.name} is not configured as read-only")
        self.name = source.name
        self.source = source
        self.urls = tuple(urls)
        from browser.playwright_reader import BrowserReadConfig, PlaywrightReader
        self.reader = PlaywrightReader(
            BrowserReadConfig(timeout_ms=timeout_ms, allowed_domains=source.domains)
        )

    def fetch(self) -> list[SourceListing]:
        return [self.reader.read_url(url) for url in self.urls]

    def validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host:
            raise ControlledSourceError("URL has no hostname")
        if not any(host == d or host.endswith("." + d) for d in self.source.domains):
            raise ControlledSourceError("URL is outside the approved source domains")
