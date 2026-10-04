from application.composition import default_registry
from datetime import datetime, timezone
from pathlib import Path

from database.store import Store
from scheduler import AutonomousScheduler


def test_scheduler_queues_due_monitoring(tmp_path, monkeypatch):
    db = tmp_path / 'worker.db'
    store = Store(db)
    monkeypatch.setenv('JOB_WORKER_TIMEZONE', 'Africa/Lagos')
    s = AutonomousScheduler(store, poll_seconds=0.01, registry=default_registry())
    result = s.run_due_once()
    assert result['monitoring_commands']
    assert store.counts()['commands'] >= 1


def test_scheduler_persists_daily_audit_once(tmp_path, monkeypatch):
    db = tmp_path / 'worker.db'
    outbox = tmp_path / 'notifications' / 'outbox'
    store = Store(db)
    # Avoid SMTP and use a deterministic local audit writer.
    monkeypatch.setenv('FULL_AUDIT_HOUR', '0')
    monkeypatch.setenv('FULL_AUDIT_MINUTE', '0')
    monkeypatch.setenv('JOB_WORKER_TIMEZONE', 'Africa/Lagos')
    monkeypatch.setattr('scheduler.send_daily_full_audit', lambda st: {'txt_path': str(outbox / 'audit.txt'), 'email_status': 'NOT_CONFIGURED'})
    outbox.mkdir(parents=True)
    s = AutonomousScheduler(store, poll_seconds=0.01, registry=default_registry())
    s.run_due_once()
    assert len([r for r in store.reports() if r['period'].startswith('full_audit:')]) == 1
    # A fresh scheduler instance must not send a second audit for the same day.
    s2 = AutonomousScheduler(store, poll_seconds=0.01, registry=default_registry())
    s2.run_due_once()
    assert len([r for r in store.reports() if r['period'].startswith('full_audit:')]) == 1
