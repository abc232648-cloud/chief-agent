from capabilities.contracts import Capability, Component, ComponentKind, Consumer, Dependency, Maturity, Mode, Node

TESTS=('tests/test_internal_reporting.py',)
from integrations.internal_reporting import CAPABILITY, SERVICE


def components():
    return (Component(SERVICE,ComponentKind.SERVICE,permissions=(CAPABILITY,),tests=TESTS),)


def capabilities():
    return (Capability(id=CAPABILITY,owner='chief',version='1.0.0',
        description='Human-confirmed Farm journal-count preview through a pinned local workflow.',
        maturity=Maturity.EXPERIMENTAL,mode=Mode.ENABLED,
        dependencies=tuple(Dependency(Node('capability',n)) for n in ('farming.records.read','chief.decision_ledger'))+
            (Dependency(Node('component','farming-recorder')),Dependency(Node('component','chief.policy'))),
        consumers=(Consumer(SERVICE,True,TESTS),),permissions=(CAPABILITY,),
        frameworks=('Current human authority, component controls and versioned Chief Policy.',),
        data_access=('Farm journal aggregate record counts only; no Job or private text.',),models=(),tests=TESTS,
        health_dependencies=('chief.database',),inputs='Explicit human confirmation and unique operation ID.',
        outputs='Correlated local preview with recorded source timestamp.',
        side_effects=('One local workflow request; operation/audit/Decision Ledger records.',),
        failure_behavior='Unverified dispatch remains UNKNOWN; never automatically resent.',
        overrides=('No model, runtime, schedule or delegated execution authority.',),
        audit=('Metadata-only audit and Decision Ledger correlation; counts in private operation storage.',)),)
