from dataclasses import dataclass
from enum import Enum
from capabilities.contracts import Node
from operations.time_integrity import ClockQuality


class HealthStatus(str, Enum):
    UNKNOWN = 'UNKNOWN'
    HEALTHY = 'HEALTHY'
    DEGRADED = 'DEGRADED'
    UNAVAILABLE = 'UNAVAILABLE'


@dataclass(frozen=True)
class Observation:
    status: HealthStatus
    observed_at: str | None
    received_at: str
    probe: str
    reason: str
    clock: ClockQuality = ClockQuality()


@dataclass(frozen=True)
class HealthResult:
    node: Node
    status: HealthStatus
    reason: str
    observation: Observation | None
    dependencies: tuple[tuple[str, HealthStatus], ...]
    desired_mode: str
    intentionally_suppressed: bool
    safe_degradation: str | None = None
