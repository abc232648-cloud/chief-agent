"""Experimental E foundations; no runtime integration or added domain grants."""
from capabilities.contracts import Capability,Component,ComponentKind,Consumer,Dependency,Maturity,Mode,Node


DEFINITIONS=(
    ('model_registry','Domain/agent model assignments and restrictive installation routing.',('tests/test_model_routing.py','tests/test_model_shadow.py','tests/test_execution_migration.py'),('chief.gateway','chief.database','chief.decision_ledger')),
    ('runbooks','Version-pinned shared SOP mechanics; no migrated Job pipeline.',('tests/test_runbooks.py','tests/test_execution_migration.py'),('chief.database','chief.evidence','chief.decision_ledger','chief.policy')),
)


def components():
    return tuple(Component('chief.'+name,ComponentKind.SERVICE,dependencies=tuple(Dependency(Node('component',d)) for d in deps),tests=tests) for name,_,tests,deps in DEFINITIONS)


def capabilities():
    return tuple(Capability(id='chief.'+name,owner='chief',version='1.0.0',description=description,maturity=Maturity.EXPERIMENTAL,mode=Mode.ENABLED,
        dependencies=(Dependency(Node('component','chief.'+name)),),consumers=(Consumer('jobs-worker',False,tests),) if name=='model_registry' else (),permissions=(),
        frameworks=('Existing Capability Registry, Policy, Evidence and Approval authority only.',),data_access=('Domain-scoped assignments/runs; global installation restrictions.',),models=(),tests=tests,
        health_dependencies=deps,inputs='Versioned contracts and trusted domain context.',outputs=description,side_effects=('Explicitly migrated additive development persistence.',),
        failure_behavior='Safe stop; no ambiguous action replay or paid fallback.',overrides=('Global model disable overrides assignments; no new grants.',),audit=('Decision Ledger and versioned SOP events.',)) for name,description,tests,deps in DEFINITIONS)
