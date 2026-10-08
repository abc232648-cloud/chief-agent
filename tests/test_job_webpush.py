import os
from pathlib import Path

import pytest

from database.store import Store
from identity.context import human_context
from identity.contracts import Principal
from notifications import webpush
from notifications.job_attention import record_job_attention


def make_store(tmp_path: Path):
    return Store(tmp_path / 'worker.db')


def valid_subscription(index=1):
    return {
        'endpoint': f'https://push.example.test/sub/{index}',
        'keys': {'p256dh': 'A' * 64, 'auth': 'B' * 24},
    }


def test_webpush_config_fails_closed_without_keys(monkeypatch):
    for key in ('CHIEF_WEB_PUSH_PUBLIC_KEY','CHIEF_WEB_PUSH_PRIVATE_KEY','CHIEF_WEB_PUSH_PRIVATE_KEY_REF','CHIEF_WEB_PUSH_SUBJECT'):
        monkeypatch.delenv(key, raising=False)
    assert webpush.client_config() == {
        'enabled': False,
        'public_key': '',
        'attention_severities': ['ACTION_REQUIRED', 'URGENT'],
    }


def test_subscription_state_is_private_and_bounded(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    monkeypatch.setattr(webpush, 'client_config', lambda env=None: {
        'enabled': True, 'public_key': 'public', 'attention_severities': ['ACTION_REQUIRED','URGENT']
    })
    for index in range(1, 11):
        webpush.subscribe(store, valid_subscription(index))
    state = tmp_path / webpush.STATE_FILE
    assert state.exists()
    subscriptions = webpush._load(store)
    assert len(subscriptions) == webpush.MAX_SUBSCRIPTIONS
    assert subscriptions[-1]['endpoint'].endswith('/10')
    if os.name != 'nt':
        assert state.stat().st_mode & 0o077 == 0


def test_info_never_attempts_background_push(tmp_path):
    store = make_store(tmp_path)
    assert webpush.deliver_job_attention(
        store, notification_id=1, title='Routine', body='Done', severity='INFO'
    ) == {'attempted': 0, 'sent': 0, 'removed': 0}


def test_job_attention_is_domain_scoped(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    monkeypatch.setattr('notifications.job_attention.deliver_job_attention', lambda *args, **kwargs: {'attempted':0,'sent':0,'removed':0})
    result = record_job_attention(store, 'Review this', 'Approval required', 'ACTION_REQUIRED', related_page='actions')
    with store._connect() as con:
        row = con.execute('SELECT * FROM notifications WHERE id=?', (result['notification_id'],)).fetchone()
    assert row['domain'] == 'jobs'
    assert row['severity'] == 'ACTION_REQUIRED'
    assert row['related_page'] == 'actions'
    assert row['presented'] == 0


def test_job_push_api_requires_jobs_principal():
    from control.api import _require_jobs_principal
    with human_context(Principal('owner','s1','Owner',('jobs',),'now')):
        assert _require_jobs_principal().id == 'owner'
    with human_context(Principal('farm','s2','Worker',('farming',),'now')):
        with pytest.raises(PermissionError):
            _require_jobs_principal()


def test_job_event_feed_never_returns_other_domains(tmp_path, monkeypatch):
    from control.api import get
    store = make_store(tmp_path)
    monkeypatch.setattr('notifications.job_attention.deliver_job_attention', lambda *args, **kwargs: {'attempted':0,'sent':0,'removed':0})
    record_job_attention(store, 'Job review', 'Needs approval', 'ACTION_REQUIRED')
    store.add_notification('System note', 'Not for Job PWA', 'INFO', domain='system')

    class Handler:
        payload = None
        def json(self, payload, code=200):
            self.payload = payload

    handler = Handler()
    with human_context(Principal('owner','s1','Owner',('jobs',),'now')):
        assert get(handler, store, tmp_path, '/api/job-push/events', '', registry=None) is True
    assert handler.payload
    assert {item['domain'] for item in handler.payload} == {'jobs'}
    assert all(item['title'] != 'System note' for item in handler.payload)


def test_job_processor_maps_ask_and_stop_to_attention(monkeypatch):
    from domains.jobs.ledger_adapter import JobCommandProcessor
    from domains.jobs.push_processor import JobPushCommandProcessor

    processor = object.__new__(JobPushCommandProcessor)
    observed = []
    processor._attention = lambda title, body, severity, **kwargs: observed.append((title, severity, kwargs.get('related_page')))
    monkeypatch.setattr(JobCommandProcessor, 'process_command', lambda self, command_id, instruction: {
        'approvals': [42],
        'results': [{'action':'submit_application','status':'AWAITING_APPROVAL'}, {'action':'pay_money','status':'BLOCKED'}],
    })

    result = JobPushCommandProcessor.process_command(processor, 9, 'apply safely')
    assert result['approvals'] == [42]
    assert ('Job Agent approval required', 'ACTION_REQUIRED', 'actions') in observed
    assert ('Job Agent STOP', 'URGENT', 'actions') in observed


def test_job_processor_maps_exception_to_urgent(monkeypatch):
    from domains.jobs.ledger_adapter import JobCommandProcessor
    from domains.jobs.push_processor import JobPushCommandProcessor

    processor = object.__new__(JobPushCommandProcessor)
    observed = []
    processor._attention = lambda title, body, severity, **kwargs: observed.append((title, severity))
    def fail(self, command_id, instruction):
        raise RuntimeError('boom')
    monkeypatch.setattr(JobCommandProcessor, 'process_command', fail)

    with pytest.raises(RuntimeError):
        JobPushCommandProcessor.process_command(processor, 10, 'broken command')
    assert observed == [('Job Agent stopped on an error', 'URGENT')]
