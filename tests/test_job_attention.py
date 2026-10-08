import pytest

from database.store import Store
from domains.jobs.attention_processor import JobAttentionCommandProcessor
from domains.jobs.ledger_adapter import JobCommandProcessor


def make_store(tmp_path):
    return Store(tmp_path / 'worker.db')


def job_notifications(store):
    from control.notifications import initialize
    initialize(store)
    with store._connect() as con:
        return [dict(row) for row in con.execute(
            "SELECT title,body,severity,domain,related_page FROM notifications WHERE domain='jobs' ORDER BY id"
        )]


def processor_with_store(store):
    processor = object.__new__(JobAttentionCommandProcessor)
    processor.store = store
    return processor


def test_approval_and_blocked_results_create_job_attention(monkeypatch, tmp_path):
    state = make_store(tmp_path)
    processor = processor_with_store(state)
    monkeypatch.setattr(JobCommandProcessor, 'process_command', lambda self, command_id, instruction: {
        'approvals': [42],
        'results': [
            {'action': 'submit_application', 'status': 'AWAITING_APPROVAL'},
            {'action': 'pay_money', 'status': 'BLOCKED'},
        ],
    })

    result = processor.process_command(9, 'private raw instruction')
    assert result['approvals'] == [42]
    rows = job_notifications(state)
    assert [(row['severity'], row['related_page']) for row in rows] == [
        ('ACTION_REQUIRED', 'actions'),
        ('URGENT', 'actions'),
    ]
    serialized = str(rows)
    assert 'private raw instruction' not in serialized
    assert 'pay_money' not in serialized


def test_safe_completion_stays_quiet(monkeypatch, tmp_path):
    state = make_store(tmp_path)
    processor = processor_with_store(state)
    monkeypatch.setattr(JobCommandProcessor, 'process_command', lambda self, command_id, instruction: {
        'approvals': [],
        'results': [{'action': 'discover_jobs', 'status': 'COMPLETED'}],
    })

    processor.process_command(10, 'discover safely')
    assert job_notifications(state) == []


def test_exception_creates_sanitized_urgent_attention_and_reraises(monkeypatch, tmp_path):
    state = make_store(tmp_path)
    processor = processor_with_store(state)

    def fail(self, command_id, instruction):
        raise RuntimeError('secret provider detail')

    monkeypatch.setattr(JobCommandProcessor, 'process_command', fail)
    with pytest.raises(RuntimeError, match='secret provider detail'):
        processor.process_command(11, 'sensitive command text')

    rows = job_notifications(state)
    assert len(rows) == 1
    assert rows[0]['severity'] == 'URGENT'
    assert rows[0]['related_page'] == 'applicationArchive'
    serialized = str(rows)
    assert 'secret provider detail' not in serialized
    assert 'sensitive command text' not in serialized
