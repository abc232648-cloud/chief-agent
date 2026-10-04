"""D mechanics are optional adapters, with no new domain grants or runtime modes."""
from capabilities.contracts import Capability,Component,ComponentKind,Consumer,Dependency,Maturity,Mode,Node


DEFINITIONS = (
    ('evidence', 'Domain-scoped evidence metadata and append-only provenance.', ('tests/test_shared_evidence.py','tests/test_job_evidence_adapter.py','tests/test_evidence_migration.py'), ('chief.database',)),
    ('data_quality', 'Quality dimensions, confidence ceilings and authority restrictions.', ('tests/test_data_quality.py','tests/test_job_evidence_adapter.py'), ('chief.evidence',)),
    ('decision_ledger', 'Reference-only decision observation; no action execution or replay.', ('tests/test_decision_ledger.py',), ('chief.evidence','chief.database','chief.policy')),
)


def components():
    return tuple(Component('chief.'+name,ComponentKind.SERVICE,
                           dependencies=tuple(Dependency(Node('component',d)) for d in deps),tests=tests)
                 for name,_,tests,deps in DEFINITIONS)


def capabilities():
    return tuple(Capability(id='chief.'+name,owner='chief',version='1.0.0',description=description,
        maturity=Maturity.EXPERIMENTAL,mode=Mode.ENABLED,
        dependencies=(Dependency(Node('component','chief.'+name)),),
        consumers=(Consumer('jobs-worker',False,tests),),permissions=(),frameworks=('Domain-scoped reference contracts; explicit isolated migration.',),
        data_access=('Private domain namespace; references rather than sensitive payloads.',),models=(),tests=tests,
        health_dependencies=deps,inputs='Active domain context and validated references.',outputs=description,
        side_effects=('Append D metadata only after explicit migration.',),failure_behavior='Evidence checks fail closed; ledger observation cannot replay actions.',
        overrides=('No new grants or autonomous permission. Shared runtime mode changes unsupported.',),audit=('Existing Job history plus additive Decision Ledger.',))
        for name,description,tests,deps in DEFINITIONS)
