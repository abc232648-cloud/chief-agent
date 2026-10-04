from dataclasses import dataclass
from datetime import timedelta
from operations.time_integrity import utc_now, utc_text, timestamp_status


@dataclass(frozen=True)
class Quality:
    dimensions: dict
    confidence: float | None
    sufficiency: str
    authority_restriction: str
    assessed_at: str
    engine_version: str = '1.0.0'

    def restrict(self, existing):
        """A ceiling over an actual policy decision, not a permission decision."""
        if existing not in {'ALLOW', 'ASK', 'BLOCK'}:
            raise ValueError('An existing policy decision is required.')
        if existing == 'BLOCK' or self.authority_restriction == 'BLOCKED':
            return 'BLOCK'
        if existing == 'ASK' or self.authority_restriction == 'REVIEW_REQUIRED':
            return 'ASK'
        return existing


def assess(record, *, now=None, max_age=timedelta(days=365), contradictions=False):
    now = utc_text(now or utc_now())
    time = record['time']
    freshness = timestamp_status(time['source_at'], now=now, max_age=max_age)
    receipt = timestamp_status(time['received_at'], now=now, max_age=timedelta.max)
    source_at_receipt = timestamp_status(time['source_at'], now=time['received_at'], max_age=timedelta.max)
    verification = record['verification']
    clock = time['clock']['quality']
    if clock not in {'UNKNOWN', 'ASSERTED', 'SYNCHRONIZED', 'UNRELIABLE', 'INVALID'}:
        clock = 'UNKNOWN'
    measured = record['truth'] == 'MEASURED'
    calibration = record['calibration'] if measured else 'NOT_APPLICABLE'
    reliability = record['reliability']
    dimensions = {
        'freshness': freshness,
        'receipt_time': receipt,
        'source_at_receipt': source_at_receipt,
        'completeness': 'COMPLETE' if time['source_at'] and record['confidence'] is not None else 'UNKNOWN',
        'conflict': 'CONFLICT' if contradictions or verification == 'DISPUTED' else 'NONE_RECORDED',
        'verification': verification,
        'clock_quality': clock,
        'calibration': calibration,
        'source_reliability': 'UNKNOWN' if reliability is None else ('LOW' if reliability < .5 else 'ASSERTED'),
    }
    adverse = (freshness in {'STALE', 'FUTURE', 'INVALID'} or receipt in {'FUTURE', 'INVALID'} or source_at_receipt in {'FUTURE', 'INVALID'}
               or verification in {'DISPUTED', 'STALE'} or contradictions
               or calibration == 'INVALID' or clock in {'UNRELIABLE', 'INVALID'}
               or (reliability is not None and reliability < .5))
    incomplete = (freshness == 'MISSING' or record['confidence'] is None or clock == 'UNKNOWN'
                  or (measured and calibration == 'UNKNOWN') or reliability is None
                  or verification in {'UNVERIFIED', 'PARTIALLY_VERIFIED'})
    confidence = record['confidence']
    if confidence is not None:
        confidence = min(confidence, reliability if reliability is not None else 1, 0.25 if adverse else 1)
    return Quality(dimensions, confidence, 'INSUFFICIENT' if adverse else ('INCOMPLETE' if incomplete else 'SUFFICIENT'),
                   'BLOCKED' if adverse else 'UNCHANGED', now)
