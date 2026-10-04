"""Checkpoint G capability declarations; planning/qualification only."""
from capabilities.contracts import Capability, Component, ComponentKind, Consumer, Dependency, Maturity, Mode, Node

UPDATE_TESTS = ('tests/test_compatibility_manifest.py','tests/test_release_package.py','tests/test_update_center.py','tests/test_checkpoint_g_boundaries.py')
RUNTIME_TESTS = ('tests/test_runtime_qualification.py','tests/test_checkpoint_g_boundaries.py')


def components():
    return (
        Component('chief.update-center', ComponentKind.SERVICE, tests=UPDATE_TESTS),
        Component('chief.runtime-qualification', ComponentKind.SERVICE, tests=RUNTIME_TESTS),
    )


def capabilities():
    return (
        Capability(
            id='chief.update_planning', owner='chief', version='1.0.0',
            description='Staging-only release compatibility, impact and regression planning; no automatic activation.',
            maturity=Maturity.LIMITED, mode=Mode.ENABLED,
            dependencies=(),
            consumers=(Consumer('chief.update-center', True, UPDATE_TESTS),), permissions=(),
            frameworks=('Existing Chief backup/restore, Capability Registry, test-integrity and human-approval requirements.',),
            data_access=('Release manifests and non-secret test/backup identities only; no private operational payloads.',),
            models=(), tests=UPDATE_TESTS, health_dependencies=(),
            inputs='Current and candidate compatibility manifests plus declared changed graph nodes.',
            outputs='Deterministic staging plan, impacted consumers and required fresh regression coverage.',
            side_effects=(),
            failure_behavior='Invalid/incompatible/under-evidenced candidates fail closed; activation is not implemented.',
            overrides=('Manual deployment remains available; no update plan may bypass backup, RBAC, Policy or fresh-test requirements.',),
            audit=('Checkpoint/report evidence outside runtime; future activation audit is not implemented here.',)),
        Capability(
            id='chief.runtime_qualification', owner='chief', version='1.0.0',
            description='Fail-closed qualification matrix for replaceable generic runtimes before any integration.',
            maturity=Maturity.EXPERIMENTAL, mode=Mode.SHADOW,
            dependencies=(),
            consumers=(Consumer('chief.runtime-qualification', True, RUNTIME_TESTS),), permissions=(),
            frameworks=('Chief remains authoritative for policy, approvals, evidence, RBAC, records and action authority.',),
            data_access=('Pinned public runtime release/documentation metadata and synthetic qualification evidence only.',),
            models=(), tests=RUNTIME_TESTS, health_dependencies=(),
            inputs='Pinned runtime candidate identity and per-gate fresh evidence.',
            outputs='Qualification matrix with mandatory blockers; no automatic runtime selection.',
            side_effects=(),
            failure_behavior='Any failed, documented-only, stale or untested hard gate blocks qualification.',
            overrides=('Prefer one primary runtime; a secondary runtime requires a separate explicit architecture decision.',),
            audit=('Qualification evidence/report only; runtime execution/integration is outside this checkpoint.',)),
    )
