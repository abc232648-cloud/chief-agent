from dataclasses import dataclass
from enum import Enum


class Gate(str, Enum):
    AUTHORITY_SECURITY = 'AUTHORITY_SECURITY'
    ISOLATION = 'ISOLATION'
    RELIABILITY = 'RELIABILITY'
    WINDOWS_COMPATIBILITY = 'WINDOWS_COMPATIBILITY'
    LINUX_FOLIO_COMPATIBILITY = 'LINUX_FOLIO_COMPATIBILITY'
    OPERATIONS = 'OPERATIONS'
    PRIVACY = 'PRIVACY'


class GateStatus(str, Enum):
    PROVEN = 'PROVEN'
    FAILED = 'FAILED'
    DOCUMENTED = 'DOCUMENTED'
    UNTESTED = 'UNTESTED'


@dataclass(frozen=True)
class Candidate:
    id: str
    product: str
    version: str
    release_date: str
    release_url: str
    documentation_urls: tuple[str, ...]
    documented_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class Evidence:
    candidate_id: str
    gate: Gate
    status: GateStatus
    environment: str
    source: str
    notes: str
    fresh: bool


@dataclass(frozen=True)
class Qualification:
    candidate_id: str
    gate_status: tuple[tuple[str, str], ...]
    qualified: bool
    blockers: tuple[str, ...]
