"""Comparison evidence is advisory and cannot authorize execution."""
from dataclasses import dataclass,field
from operations.time_integrity import utc_now,utc_text


@dataclass(frozen=True)
class PolicyComparison:
    action: str
    legacy: str
    candidate: str | None
    status: str
    pack_versions: tuple[tuple[str, str], ...]
    error_type: str | None = None
    received_at: str = field(default_factory=lambda:utc_text(utc_now()))
