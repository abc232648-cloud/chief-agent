from dataclasses import dataclass, field
from enum import Enum
import math
import re
from operations.time_integrity import TimeReceipt, utc_now, utc_text


class TruthType(str, Enum):
    MEASURED = 'MEASURED'
    OBSERVED = 'OBSERVED'
    DOCUMENTED = 'DOCUMENTED'
    INFERRED = 'INFERRED'


class Verification(str, Enum):
    UNVERIFIED = 'UNVERIFIED'
    PARTIALLY_VERIFIED = 'PARTIALLY_VERIFIED'
    VERIFIED = 'VERIFIED'
    DISPUTED = 'DISPUTED'
    STALE = 'STALE'


def token(value):
    """Opaque identifiers/codes only, never URLs, paths or narrative payloads."""
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_:.\-]{1,160}', value):
        raise ValueError('Expected a bounded opaque reference or code.')
    return value


def probability(value):
    if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 1):
        raise ValueError('Confidence/reliability must be unknown or between zero and one.')
    return value


@dataclass(frozen=True)
class Evidence:
    truth: TruthType
    source_owner: str
    source_kind: str
    source_ref: str
    content_ref: str
    time: TimeReceipt = field(default_factory=lambda: TimeReceipt(utc_text(utc_now())))
    verification: Verification = Verification.UNVERIFIED
    confidence: float | None = None
    reliability: float | None = None
    calibration: str = 'UNKNOWN'
    verification_basis: str | None = None

    def __post_init__(self):
        object.__setattr__(self, 'truth', TruthType(self.truth))
        object.__setattr__(self, 'verification', Verification(self.verification))
        for value in (self.source_owner, self.source_kind, self.source_ref, self.content_ref):
            token(value)
        probability(self.confidence)
        probability(self.reliability)
        if self.calibration not in {'UNKNOWN', 'VALID', 'INVALID', 'NOT_APPLICABLE'}:
            raise ValueError('Unknown calibration assertion.')
        if not isinstance(self.time, TimeReceipt):
            raise ValueError('A validated time receipt is required.')
        if self.verification_basis is not None:
            token(self.verification_basis)
        if self.verification in {Verification.VERIFIED, Verification.PARTIALLY_VERIFIED} and not self.verification_basis:
            raise ValueError('Verification requires a basis reference; never inferred from confirmation.')
