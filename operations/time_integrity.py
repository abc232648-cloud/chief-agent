"""UTC at new boundaries; legacy timestamps require an explicit adapter.

Clock metadata is an assertion supplied by a caller, not a measured guarantee.
Monotonic time, rather than this wall-clock contract, belongs in deadlines.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math


def utc_now():
    return datetime.now(timezone.utc)


def aware_utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('An explicit timezone is required.')
    return value.astimezone(timezone.utc)


def utc_text(value):
    return aware_utc(value).isoformat(timespec='microseconds').replace('+00:00', 'Z')


@dataclass(frozen=True)
class ClockQuality:
    clock_id: str = 'local-wall-clock'
    quality: str = 'UNKNOWN'
    offset_seconds: float | None = None
    uncertainty_seconds: float | None = None

    def __post_init__(self):
        if not self.clock_id or not self.quality:
            raise ValueError('Clock identity and quality must be explicit.')
        for number in (self.offset_seconds, self.uncertainty_seconds):
            if number is not None and not math.isfinite(number):
                raise ValueError('Clock estimates must be finite.')
        if self.uncertainty_seconds is not None and self.uncertainty_seconds < 0:
            raise ValueError('Clock uncertainty cannot be negative.')


@dataclass(frozen=True)
class TimeReceipt:
    received_at: str
    source_at: str | None = None
    clock: ClockQuality = ClockQuality()

    def __post_init__(self):
        object.__setattr__(self, 'received_at', utc_text(self.received_at))
        if self.source_at is not None:
            object.__setattr__(self, 'source_at', utc_text(self.source_at))

    def source_status(self, *, max_age, future_tolerance=timedelta(seconds=5)):
        return timestamp_status(self.source_at, now=self.received_at,
                                max_age=max_age, future_tolerance=future_tolerance)


def timestamp_status(value, *, now, max_age, future_tolerance=timedelta(seconds=5)):
    """Classify observation freshness without changing intent or stored history."""
    current = aware_utc(now)
    if max_age < timedelta(0) or future_tolerance < timedelta(0):
        raise ValueError('Freshness limits must be nonnegative.')
    if value is None:
        return 'MISSING'
    try:
        observed = aware_utc(value)
    except (TypeError, ValueError, OverflowError):
        return 'INVALID'
    if observed > current + future_tolerance:
        return 'FUTURE'
    if current - observed > max_age:
        return 'STALE'
    return 'CURRENT'
