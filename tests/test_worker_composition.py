"""Exercise real worker startup wiring without starting a background service."""
from database.store import Store
from worker import runner


def test_worker_startup_composes_domains_and_preserves_interrupted_review(tmp_path, monkeypatch):
    path = tmp_path / 'worker.db'
    store = Store(path)
    from tests.checkpoint_f_fixture import secure_store
    secure_store(store)
    action = store.add_action('Interrupted fixture', 'submit_application')
    store.resolve_action(action, 'APPROVED')
    assert store.next_approved_action()['id'] == action
    monkeypatch.setenv('JOB_WORKER_DB', str(path))
    def gateway(*, store):
        assert store.path == path
        return object()
    monkeypatch.setattr(runner, 'build_gateway', gateway)
    # Keep the heartbeat thread from outliving this disposable test database.
    monkeypatch.setattr('threading.Thread.start', lambda self: None)
    from scheduler import AutonomousScheduler
    monkeypatch.setattr(AutonomousScheduler,'run_due_once',lambda *a:__import__('pytest').fail('Worker must not schedule.'))
    def stop(processor):
        assert list(processor.registry.domains) == ['jobs', 'farming']
        assert processor.controls.allowed('jobs')
        assert processor.controls.allowed('farming')
        raise KeyboardInterrupt
    monkeypatch.setattr(runner, 'run_approved_once', stop)
    assert runner.main() == 0
    assert store.get_action(action)['status'] == 'REVIEW'
    assert store.next_approved_action() is None


def test_worker_missing_identity_schema_stops_before_provider_or_queue(tmp_path,monkeypatch):
    import pytest
    from identity.contracts import SetupRequired
    path=tmp_path/'pre-f.db';store=Store(path)
    action=store.add_action('Synthetic pending approval','submit_application')
    store.resolve_action(action,'APPROVED')
    monkeypatch.setenv('JOB_WORKER_DB',str(path))
    monkeypatch.setattr(runner,'build_gateway',lambda **kwargs:pytest.fail('Provider must not be constructed'))
    with pytest.raises(SetupRequired):runner.main()
    assert store.get_action(action)['status']=='APPROVED'
