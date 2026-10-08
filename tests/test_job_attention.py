from types import SimpleNamespace

import pytest

from database.store import Store
from policy.rules import Decision
from worker.command_processor import CommandProcessor


def make_store(tmp_path):
    return Store(tmp_path / 'worker.db')


def notifications(store):
    from control.notifications import initialize
    initialize(store)
    with store._connect() as con:
        return [dict(row) for row in con.execute(
            "SELECT title,body,severity,domain,related_page FROM notifications ORDER BY id"
        )]


def processor_with_plan(store, plan, decisions):
    processor = object.__new__(CommandProcessor)
    processor.store = store
    processor._plan = lambda instruction: plan
    processor._execute_low_risk = lambda action, payload: {'status': 'COMPLETED'}

    def decide(request):
        decision = decisions[request.action] if isinstance(decisions, dict) else decisions
        return SimpleNamespace(decision=decision, reason='test policy decision')

    processor.gate = SimpleNamespace(decide=decide)
    return processor


def test_approval_and_blocked_results_create_only_job_attention(tmp_path):
    state = make_store(tmp_path)
    processor = processor_with_plan(
        state,
        {
            'summary': 'mixed result',
            'actions': [
                {'action': 'submit_application', 'payload': {}, 'reason': 'needs approval'},
                {'action': 'pay_money', 'payload': {}, 'reason': 'must stop'},
            ],
        },
        {'submit_application': Decision.ASK, 'pay_money': Decision.BLOCK},
    )

    result = processor.process_command(9, 'private raw instruction')
    assert len(result['approvals']) == 1
    rows = notifications(state)
    assert [(row['severity'], row['domain'], row['related_page']) for row in rows] == [
        ('ACTION_REQUIRED', 'jobs', 'actions'),
        ('URGENT', 'jobs', 'actions'),
    ]
    serialized = str(rows)
    assert 'private raw instruction' not in serialized
    assert 'pay_money' not in serialized
    assert 'WARNING' not in serialized
    assert 'ERROR' not in serialized


def test_safe_completion_stays_quiet(tmp_path):
    state = make_store(tmp_path)
    processor = processor_with_plan(
        state,
        {'summary': 'safe', 'actions': [{'action': 'discover_jobs', 'payload': {}, 'reason': 'safe'}]},
        Decision.ALLOW,
    )

    processor.process_command(10, 'discover safely')
    assert notifications(state) == []


def test_block_without_approval_creates_single_stop_notification(tmp_path):
    state = make_store(tmp_path)
    processor = processor_with_plan(
        state,
        {'summary': 'blocked', 'actions': [{'action': 'pay_money', 'payload': {}, 'reason': 'forbidden'}]},
        Decision.BLOCK,
    )

    processor.process_command(11, 'do not expose this')
    rows = notifications(state)
    assert len(rows) == 1
    assert rows[0]['title'] == 'Job Agent STOP'
    assert rows[0]['severity'] == 'URGENT'
    assert rows[0]['domain'] == 'jobs'
    assert 'do not expose this' not in str(rows)


def test_exception_creates_single_sanitized_urgent_attention_and_reraises(tmp_path):
    state = make_store(tmp_path)
    processor = object.__new__(CommandProcessor)
    processor.store = state

    def fail(_instruction):
        raise RuntimeError('secret provider detail')

    processor._plan = fail
    with pytest.raises(RuntimeError, match='secret provider detail'):
        processor.process_command(12, 'sensitive command text')

    rows = notifications(state)
    assert len(rows) == 1
    assert rows[0]['title'] == 'Job Agent stopped on an error'
    assert rows[0]['severity'] == 'URGENT'
    assert rows[0]['domain'] == 'jobs'
    assert rows[0]['related_page'] == 'applicationArchive'
    serialized = str(rows)
    assert 'secret provider detail' not in serialized
    assert 'sensitive command text' not in serialized
    assert 'ERROR' not in serialized
