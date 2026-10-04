import pytest

from agents.registry import AgentRegistry
from application.composition import default_registry
from control.agents import AgentControls
from database.store import Store
from domains.contracts import AgentDefinition, DomainAction, DomainDefinition
from domains.farming import definition as farming
from domains.jobs import definition as jobs
from domains.runtime import DomainRuntime
from domains.storage import deliver_due_reminders
from scheduler import AutonomousScheduler


def test_composition_preserves_original_registration_order_and_metadata():
    expected = AgentRegistry()
    for factory in (jobs, farming):
        expected.register(*factory())
    actual = default_registry()
    assert list(actual.domains) == ['jobs', 'farming']
    assert actual.domains == expected.domains
    assert actual.agents == expected.agents
    assert actual.describe() == expected.describe()
    assert default_registry() is not actual


def test_core_requires_explicit_registration(tmp_path):
    store = Store(tmp_path / 'db')
    with pytest.raises(TypeError):
        DomainRuntime(store)
    with pytest.raises(TypeError):
        AgentControls(store)
    with pytest.raises(TypeError):
        AutonomousScheduler(store)
    runtime = DomainRuntime(store, registry=AgentRegistry())
    assert runtime.registry.describe() == []
    assert runtime.controls.overview()['agents'] == []


def test_custom_only_domain_survives_controls_scheduler_and_reminders(tmp_path):
    registry = AgentRegistry()
    def remind(storage, payload):
        storage.remind('Synthetic reminder', 0)
        return {'status': 'COMPLETED'}
    action = DomainAction('remind', 'Remind', 'demo.reminders.write', (), remind)
    registry.register(DomainDefinition('demo', 'Demo', 'Fixture', (action,)),
                      AgentDefinition('demo-worker', 'demo', frozenset({'demo.reminders.write'})))
    store = Store(tmp_path / 'db')
    runtime = DomainRuntime(store, registry=registry)
    queued = runtime.queue('demo', 'remind', {})
    assert runtime.process_command(queued['command_id'], 'fixture')['status'] == 'COMPLETED'
    runtime.controls.change('demo', {'running': False})
    assert deliver_due_reminders(store, runtime.controls) == 0
    runtime.controls.change('demo', {'running': True})
    assert deliver_due_reminders(store, runtime.controls) == 1
    assert deliver_due_reminders(store, runtime.controls) == 0
    scheduler = AutonomousScheduler(store, registry=registry)
    scheduler.audit_hour = 24  # No daily delivery during this dispatch test.
    assert scheduler.run_due_once()['monitoring_commands'] == []
    assert [a['id'] for a in runtime.controls.overview()['agents']] == ['demo']
