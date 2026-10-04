from datetime import datetime, timedelta, timezone
import pytest
from operations.time_integrity import ClockQuality, TimeReceipt, aware_utc, timestamp_status, utc_now, utc_text

NOW = '2026-09-24T12:00:00Z'


def test_offsets_normalize_and_roundtrip():
    assert utc_text('2026-09-24T13:00:00+01:00') == '2026-09-24T12:00:00.000000Z'
    assert aware_utc(utc_text(utc_now())).tzinfo == timezone.utc


@pytest.mark.parametrize('value', ['2026-09-24 12:00:00', datetime(2026, 9, 24), 123, None])
def test_new_storage_contract_rejects_naive_and_non_dates(value):
    with pytest.raises(ValueError):
        utc_text(value)


@pytest.mark.parametrize('value,status', [
    (None, 'MISSING'), ('bad', 'INVALID'), ('2026-09-24 12:00:00', 'INVALID'),
    ('2026-09-24T11:59:29Z', 'STALE'), ('2026-09-24T11:59:30Z', 'CURRENT'),
    ('2026-09-24T12:00:05Z', 'CURRENT'), ('2026-09-24T12:00:06Z', 'FUTURE')])
def test_freshness_boundaries(value, status):
    assert timestamp_status(value, now=NOW, max_age=timedelta(seconds=30)) == status


def test_source_and_receipt_are_distinct_and_metadata_is_optional():
    receipt = TimeReceipt(NOW, '2026-09-24T11:00:00Z')
    assert receipt.received_at != receipt.source_at
    assert receipt.source_status(max_age=timedelta(minutes=5)) == 'STALE'
    assert receipt.clock.quality == 'UNKNOWN'
    assert receipt.clock.offset_seconds is None
    assert TimeReceipt(NOW).source_status(max_age=timedelta(0)) == 'MISSING'


@pytest.mark.parametrize('kwargs', [{'offset_seconds': float('nan')}, {'uncertainty_seconds': -1},
                                  {'uncertainty_seconds': float('inf')}, {'clock_id': ''}])
def test_invalid_clock_metadata_rejected(kwargs):
    with pytest.raises(ValueError):
        ClockQuality(**kwargs)


def test_negative_limits_rejected():
    with pytest.raises(ValueError):
        timestamp_status(NOW, now=NOW, max_age=timedelta(seconds=-1))


def test_future_source_does_not_change_receipt():
    receipt = TimeReceipt(NOW, '2026-09-25T12:00:00Z', ClockQuality(quality='UNVERIFIED', offset_seconds=0.2))
    assert receipt.source_status(max_age=timedelta(hours=1)) == 'FUTURE'
    assert receipt.received_at == '2026-09-24T12:00:00.000000Z'
