from datetime import datetime, timezone
import json
from zoneinfo import ZoneInfo

from domains.farming import setup
from domains.farming import timezone as farm_time
from tests.test_farm_setup import entity
import pytest
from pathlib import Path


def test_new_uk_farm_detects_and_pins_its_own_zone_with_dst(dashboard, monkeypatch):
    monkeypatch.setattr(farm_time, 'host_zone', lambda: ZoneInfo('Europe/London'))
    store, owner = dashboard.store, dashboard.credentials['principal']
    assert setup.overview(store, owner)['timezone'] == 'Europe/London'
    with store._connect() as con:
        schema = list(map(tuple, con.execute('SELECT name,sql FROM sqlite_master ORDER BY name')))
        assert not con.execute('SELECT 1 FROM domain_records WHERE kind=?', (farm_time.KIND,)).fetchone()
    setup.append(store, owner, entity())
    monkeypatch.setattr(farm_time, 'host_zone', lambda: ZoneInfo('America/New_York'))
    zone = setup.farm_zone(store)
    assert zone.key == 'Europe/London'
    assert datetime(2026, 1, 1, tzinfo=zone).utcoffset().total_seconds() == 0
    assert datetime(2026, 7, 1, tzinfo=zone).utcoffset().total_seconds() == 3600
    with store._connect() as con:
        assert schema == list(map(tuple, con.execute('SELECT name,sql FROM sqlite_master ORDER BY name')))
        assert con.execute('SELECT COUNT(*) FROM domain_records WHERE kind=?', (farm_time.KIND,)).fetchone()[0] == 1


def test_existing_lagos_history_is_not_reinterpreted_on_a_uk_host(dashboard, monkeypatch):
    store = dashboard.store
    raw = json.dumps({'legacy': 'Synthetic existing Farm record'})
    with store._connect() as con:
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming','synthetic_legacy',?,0)", (raw,))
    monkeypatch.setattr(farm_time, 'host_zone', lambda: ZoneInfo('Europe/London'))
    assert setup.farm_zone(store).key == 'Africa/Lagos'
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        assert farm_time.pin(con)['basis'] == 'LEGACY_HISTORY'
        assert con.execute("SELECT data_json FROM domain_records WHERE kind='synthetic_legacy'").fetchone()[0] == raw


def test_farm_day_boundary_uses_saved_zone(dashboard, monkeypatch):
    from domains.farming import brief, journal
    from tests.test_farm_journal import record
    monkeypatch.setattr(farm_time, 'host_zone', lambda: ZoneInfo('America/New_York'))
    store, owner = dashboard.store, dashboard.credentials['principal']
    journal.append(store, owner, record(kind='eggs_collected', quantity='7', observed_at='2026-01-02T02:00:00Z'))
    assert brief.overview(store, owner, '2026-01-01')['timezone'] == 'America/New_York'
    assert sum(int(m['quantity']) for m in brief.overview(store, owner, '2026-01-01')['metrics']) == 7
    assert not brief.overview(store, owner, '2026-01-02')['metrics']


def test_scheduler_follows_host_but_honours_explicit_override(tmp_path, monkeypatch):
    from scheduler import AutonomousScheduler
    from application.composition import default_registry
    from database.store import Store
    monkeypatch.delenv('JOB_WORKER_TIMEZONE', raising=False)
    monkeypatch.setattr('operations.local_timezone.host_zone', lambda: ZoneInfo('Europe/London'))
    scheduler = AutonomousScheduler(Store(tmp_path/'scheduler.db'), registry=default_registry())
    assert scheduler.timezone.key == 'Europe/London'
    monkeypatch.setattr('operations.local_timezone.host_zone', lambda: ZoneInfo('Asia/Tokyo'))
    assert scheduler.timezone.key == 'Asia/Tokyo'
    monkeypatch.setenv('JOB_WORKER_TIMEZONE', 'America/New_York')
    explicit = AutonomousScheduler(scheduler.store, registry=default_registry())
    assert explicit.timezone.key == 'America/New_York'


@pytest.mark.parametrize('wall,expected', [('2026-03-29T01:30', False), ('2026-10-25T01:30', False), ('2026-07-01T09:00', True)])
def test_dst_deadlines_never_invent_a_late_report(wall, expected):
    assert (farm_time.unambiguous_deadline(datetime.fromisoformat(wall), ZoneInfo('Europe/London')) is not None) is expected


@pytest.mark.parametrize('zone,wall,utc', [
    ('Europe/London', '2026-01-01T09:00', '2026-01-01T09:00:00.000Z'),
    ('Europe/London', '2026-07-01T09:00', '2026-07-01T08:00:00.000Z'),
    ('America/New_York', '2026-01-01T09:00', '2026-01-01T14:00:00.000Z'),
    ('Asia/Kathmandu', '2026-01-01T09:00', '2026-01-01T03:15:00.000Z'),
])
def test_browser_local_input_round_trip(zone, wall, utc):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(timezone_id=zone)
            page.add_script_tag(path=str(Path(__file__).resolve().parents[1]/'static/chief-time.js'))
            assert page.evaluate('(value)=>chiefLocalISO(value)', wall) == utc
            assert page.evaluate('(value)=>chiefLocalInput(value)', utc) == wall
        finally:
            browser.close()


def test_browser_dst_gap_rejected_and_repeated_hour_requires_choice():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(timezone_id='Europe/London')
            page.add_script_tag(path=str(Path(__file__).resolve().parents[1]/'static/chief-time.js'))
            assert page.evaluate("()=>{try{chiefLocalISO('2026-03-29T01:30');return false}catch(e){return e.message.includes('does not exist')}}")
            page.evaluate("window.prompt=()=>null")
            assert page.evaluate("()=>{try{chiefLocalISO('2026-10-25T01:30');return false}catch(e){return e.message.includes('occurrence')}}")
            page.evaluate("window.prompt=()=>'2'")
            assert page.evaluate("chiefLocalISO('2026-10-25T01:30')") == '2026-10-25T01:30:00.000Z'
        finally:
            browser.close()


def test_actual_host_returns_iana_timezone():
    from operations.local_timezone import host_zone
    detected = host_zone()
    assert ZoneInfo(detected.key).key == detected.key
