from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class SourceConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ApprovedSource:
    name: str
    domains: tuple[str, ...]
    enabled: bool = True
    read_only: bool = True
    start_urls: tuple[str, ...] = ()

    collector: str = "playwright"

    def validate(self) -> None:
        if self.collector not in {"playwright", "scrapy_jobposting", "scrapy_greenhouse", "scrapy_lever"}:
            raise SourceConfigError("Unsupported source collector")
        if not self.name.strip():
            raise SourceConfigError("Source name cannot be empty")
        if not self.domains:
            raise SourceConfigError(f"Source {self.name!r} has no approved domains")
        if not self.read_only:
            raise SourceConfigError(f"Source {self.name!r} must be read_only")
        for domain in self.domains:
            if not domain or "/" in domain or "://" in domain:
                raise SourceConfigError(f"Invalid approved domain: {domain!r}")
        for url in self.start_urls:
            parsed=urlparse(url)
            host=(parsed.hostname or '').lower()
            if parsed.scheme != 'https' or not any(host == d or host.endswith('.'+d) for d in self.domains):
                raise SourceConfigError(f'Start URL must be HTTPS on an existing source domain: {url!r}')


def load_sources(path: str | Path) -> tuple[ApprovedSource, ...]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceConfigError(f"Could not load source config: {exc}") from exc

    entries = data.get("sources")
    if not isinstance(entries, list):
        raise SourceConfigError("sources must be a list")

    result: list[ApprovedSource] = []
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise SourceConfigError("Each source entry must be an object")
        source = ApprovedSource(
            name=str(entry.get("name", "")),
            domains=tuple(str(x).lower().strip() for x in entry.get("domains", [])),
            enabled=bool(entry.get("enabled", True)),
            read_only=bool(entry.get("read_only", True)),
            start_urls=tuple(str(x) for x in entry.get('start_urls', [])),
            collector=str(entry.get("collector", "playwright")),
        )
        source.validate()
        if source.name in names:
            raise SourceConfigError(f"Duplicate source name: {source.name}")
        names.add(source.name)
        result.append(source)
    return tuple(result)
