from datetime import datetime, timezone

import pytest

from application.composition import default_catalogs
from capabilities.contracts import Mode, Node
from control.health import SystemHealth
from control.health_contracts import HealthStatus, Observation
from control.module_lifecycle import (
    ModuleLifecycle,
    ModuleLifecycleEvidence,
    ModuleState,
)
from checkpoint_c_fixture import migrated, unmigrated


NOW = datetime(2026, 10, 9, 2, 30, tzinfo=timezone.utc)


def observation(status=HealthStatus.HEALTHY):
    return Observation(
        status,
        '2026-10-09T02:30:00Z',
        '2026-10-09T02:30:00Z',
        'synthetic-module-probe',
        f'Synthetic {status.value.lower()} observation',
    )


def full_probes(controls):
    return {node: (lambda: observation()) for node in controls.catalog.graph.nodes}


def lifecycle_with_health(migrated, status):
    controls = migrated.services.controls
    manifests = default_catalogs().manifests
    node = Node('component', manifests.runtime_agent_id('jobs'))
    probes = full_probes(controls)
    probes[node] = lambda: observation(status)
    health = SystemHealth(controls, probes=probes, now=lambda: NOW)
    return ModuleLifecycle(manifests, controls, health)


def test_lifecycle_contract_contains_exact_public_states():
    assert {state.value for state in ModuleState} == {
        'NOT_INSTALLED', 'INSTALLED', 'DISABLED', 'ENABLED', 'DEGRADED',
        'UPDATE_AVAILABLE', 'INCOMPATIBLE',
    }


def test_application_composes_read_only_lifecycle_for_installed_modules(migrated):
    lifecycle = migrated.services.lifecycle
    assert lifecycle is not None
    described = {item['module_id']: item for item in lifecycle.describe()}
    assert {'jobs', 'farming'} <= set(described)
    jobs = described['jobs']
    assert jobs['runtime_agent_id'] == lifecycle.manifests.runtime_agent_id('jobs')
    assert jobs['version'] == lifecycle.manifests.get('jobs').version
    assert jobs['state'] == 'ENABLED'
    assert jobs['health'] == 'UNKNOWN'
    assert jobs['compatible'] is True
    assert jobs['authority'] == 'CHIEF_DERIVED'


def test_unknown_health_does_not_fake_degradation(migrated):
    snapshot = migrated.services.lifecycle.snapshot('jobs')
    assert snapshot.health == HealthStatus.UNKNOWN.value
    assert snapshot.state is ModuleState.ENABLED


def test_unknown_module_is_not_installed_and_cannot_receive_evidence(migrated):
    lifecycle = migrated.services.lifecycle
    snapshot = lifecycle.snapshot('security')
    assert snapshot.state is ModuleState.NOT_INSTALLED
    assert snapshot.version is None and snapshot.runtime_agent_id is None
    with pytest.raises(ValueError, match='not installed'):
        lifecycle.snapshot('security', evidence=ModuleLifecycleEvidence(activated=False))


def test_installed_before_activation_is_representable_without_mutating_runtime(migrated):
    lifecycle = migrated.services.lifecycle
    runtime_id = lifecycle.manifests.runtime_agent_id('jobs')
    node = Node('component', runtime_id)
    before = lifecycle.controls.desired(node)
    snapshot = lifecycle.snapshot('jobs', evidence=ModuleLifecycleEvidence(activated=False))
    after = lifecycle.controls.desired(node)
    assert snapshot.state is ModuleState.INSTALLED
    assert snapshot.desired_mode == 'ENABLED'
    assert after == before


def test_existing_component_control_remains_authority_for_disabled_state(migrated):
    lifecycle = migrated.services.lifecycle
    controls = migrated.services.controls
    node = Node('component', lifecycle.manifests.runtime_agent_id('jobs'))
    before = controls.desired(node).revision
    preview = controls.preview(node, Mode.DISABLED)
    controls.transition(preview, actor='test', reason='A7 lifecycle projection test', confirmed=True)
    snapshot = lifecycle.snapshot('jobs')
    assert snapshot.state is ModuleState.DISABLED
    assert snapshot.desired_mode == 'DISABLED'
    assert snapshot.desired_revision == before + 1


@pytest.mark.parametrize('health_status', [HealthStatus.DEGRADED, HealthStatus.UNAVAILABLE])
def test_unhealthy_enabled_module_projects_degraded(migrated, health_status):
    lifecycle = lifecycle_with_health(migrated, health_status)
    snapshot = lifecycle.snapshot('jobs')
    assert snapshot.state is ModuleState.DEGRADED
    assert snapshot.health == health_status.value
    assert snapshot.desired_mode == 'ENABLED'


def test_validated_update_is_projection_only_and_lower_priority_than_degradation(migrated):
    evidence = ModuleLifecycleEvidence(validated_update_version='9.9.9')
    healthy = lifecycle_with_health(migrated, HealthStatus.HEALTHY).snapshot('jobs', evidence=evidence)
    degraded = lifecycle_with_health(migrated, HealthStatus.DEGRADED).snapshot('jobs', evidence=evidence)
    assert healthy.state is ModuleState.UPDATE_AVAILABLE
    assert healthy.update_version == '9.9.9'
    assert degraded.state is ModuleState.DEGRADED
    assert degraded.update_version == '9.9.9'


def test_legacy_incompatible_evidence_fails_closed_without_a8_evaluator(migrated):
    lifecycle = lifecycle_with_health(migrated, HealthStatus.HEALTHY)
    controls = lifecycle.controls
    node = Node('component', lifecycle.manifests.runtime_agent_id('jobs'))
    controls.transition(
        controls.preview(node, Mode.DISABLED),
        actor='test',
        reason='Synthetic disabled state',
        confirmed=True,
    )
    evidence = ModuleLifecycleEvidence(
        compatible=False,
        compatibility_reason='Synthetic compatibility blocker.',
        validated_update_version='9.9.9',
    )
    snapshot = lifecycle.snapshot('jobs', evidence=evidence)
    assert snapshot.state is ModuleState.INCOMPATIBLE
    assert snapshot.reason == 'Synthetic compatibility blocker.'
    assert snapshot.desired_mode == 'DISABLED'


def test_a8_compatibility_cannot_be_overridden_by_lifecycle_evidence(migrated):
    lifecycle = migrated.services.lifecycle
    with pytest.raises(ValueError, match='owned by the A8 evaluator'):
        lifecycle.snapshot(
            'jobs',
            evidence=ModuleLifecycleEvidence(compatible=True),
        )


def test_lifecycle_evidence_is_bounded_and_cannot_target_unknown_describe_entries(migrated):
    with pytest.raises(ValueError, match='explicit reason'):
        ModuleLifecycleEvidence(compatible=False)
    with pytest.raises(ValueError, match='differ'):
        migrated.services.lifecycle.snapshot(
            'jobs',
            evidence=ModuleLifecycleEvidence(
                validated_update_version=migrated.services.lifecycle.manifests.get('jobs').version,
            ),
        )
    with pytest.raises(ValueError, match='not installed'):
        migrated.services.lifecycle.describe(evidence={'security': ModuleLifecycleEvidence()})


def test_projection_has_no_lifecycle_persistence_or_mode_side_effect(migrated):
    lifecycle = migrated.services.lifecycle
    node = Node('component', lifecycle.manifests.runtime_agent_id('jobs'))
    before = lifecycle.controls.desired(node)
    with migrated.store._connect() as con:
        tables_before = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    lifecycle.describe(evidence={'jobs': ModuleLifecycleEvidence(validated_update_version='9.9.9')})
    with migrated.store._connect() as con:
        tables_after = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables_after == tables_before
    assert lifecycle.controls.desired(node) == before
