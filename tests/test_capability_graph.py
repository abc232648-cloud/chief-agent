from dataclasses import replace
from pathlib import Path

import pytest

from capabilities.contracts import Component, ComponentKind, Consumer, Dependency, Node
from capabilities.registry import CapabilityRegistry
from capabilities.regression import regression_plan
from test_capability_registry import capability, components


def shared_registry(reverse=False):
    # Synthetic shared owner and two consumers; no concrete domain imports.
    owner, reader = components()
    other = Component('other-reader', ComponentKind.AGENT, permissions=('sample.read',))
    hardware = Component('backend', ComponentKind.SERVICE, tests=('tests/test_database.py',))
    shared = capability(consumers=(), dependencies=(Dependency(Node('component', 'backend')),))
    dependent = capability(id='sample.pipeline', permissions=(),
                           dependencies=(Dependency(Node('capability', 'sample.read')),),
                           consumers=(Consumer('reader', True, ('tests/test_domains.py',)),
                                      Consumer('other-reader', True, ('tests/test_chief_controls.py',))))
    parts, caps = (owner, reader, other, hardware), (shared, dependent)
    return CapabilityRegistry(parts[::-1] if reverse else parts, caps[::-1] if reverse else caps)


def test_transitive_impact_includes_every_required_consumer_deterministically():
    a, b = shared_registry(), shared_registry(True)
    change = Node('component', 'backend')
    assert a.graph.impact([change]) == b.graph.impact([change])
    assert a.graph.topological_order() == b.graph.topological_order()
    assert a.required_consumers([change]) == ('other-reader', 'reader')
    assert Node('capability', 'sample.pipeline') in a.graph.dependents(change)
    assert Node('component', 'reader') in a.graph.dependents(change)
    assert a.graph.dependencies(Node('capability', 'sample.pipeline')) == (
        Node('capability', 'sample.read'), Node('component', 'backend'))
    assert a.graph.dependencies(Node('capability', 'sample.pipeline'), transitive=False) == (Node('capability', 'sample.read'),)
    plan = regression_plan(a, [change], candidate='candidate-a')
    assert plan.required_tests == ('tests/test_chief_controls.py', 'tests/test_database.py', 'tests/test_domains.py')
    assert plan.id == regression_plan(b, [change, change], candidate='candidate-a').id


@pytest.mark.parametrize('case', ['capabilities', 'self', 'components', 'consumer'])
def test_cycles_are_rejected_across_all_edge_types(case):
    caps = [capability()]
    parts = list(components())
    if case == 'self':
        caps[0] = capability(dependencies=(Dependency(Node('capability', 'sample.read')),))
    elif case == 'capabilities':
        caps = [capability(dependencies=(Dependency(Node('capability', 'sample.other')),)),
                capability(id='sample.other', dependencies=(Dependency(Node('capability', 'sample.read')),))]
    elif case == 'components':
        parts += [Component('a', ComponentKind.SERVICE, dependencies=(Dependency(Node('component', 'b')),)),
                  Component('b', ComponentKind.SERVICE, dependencies=(Dependency(Node('component', 'a')),))]
    else:
        caps[0] = capability(health_dependencies=('reader',))
    with pytest.raises(ValueError, match='cycle'):
        CapabilityRegistry(parts, caps)


def test_unknown_change_fails_instead_of_returning_an_empty_test_plan():
    with pytest.raises(ValueError, match='Unknown graph'):
        regression_plan(shared_registry(), [Node('capability', 'missing')], candidate='candidate-a')
    with pytest.raises(ValueError, match='at least one'):
        regression_plan(shared_registry(), [], candidate='candidate-a')
    with pytest.raises(ValueError, match='candidate identity'):
        regression_plan(shared_registry(), [Node('capability', 'sample.read')], candidate='')


def test_required_consumer_failure_missing_skip_and_wrong_candidate_block_coverage():
    registry = shared_registry()
    plan = regression_plan(registry, [Node('component', 'backend')], candidate='candidate-a')
    outcomes = dict.fromkeys(plan.required_tests, 'PASSED')
    plan.require_passes(plan.id, outcomes)
    for status in ('FAILED', 'SKIPPED', 'BLOCKED', 'UNKNOWN'):
        with pytest.raises(ValueError, match='not passed'):
            plan.require_passes(plan.id, {**outcomes, 'tests/test_chief_controls.py': status})
    with pytest.raises(ValueError, match='not passed'):
        plan.require_passes(plan.id, {'tests/test_domains.py': 'PASSED'})
    next_candidate = regression_plan(registry, plan.changes, candidate='candidate-b')
    with pytest.raises(ValueError, match='different candidate'):
        next_candidate.require_passes(plan.id, outcomes)


def test_optional_consumer_is_visible_but_does_not_satisfy_required_coverage():
    cap = capability(consumers=(Consumer('reader', False, ('tests/test_policy.py',)),))
    registry = CapabilityRegistry(components(), [cap])
    plan = regression_plan(registry, [Node('capability', cap.id)], candidate='a')
    assert plan.required_consumers == ()
    assert plan.optional_tests == ('tests/test_policy.py',)
    with pytest.raises(ValueError, match='not passed'):
        plan.require_passes(plan.id, {'tests/test_policy.py': 'PASSED'})


def test_missing_test_file_blocks_plan_use(tmp_path):
    plan = regression_plan(shared_registry(), [Node('component', 'backend')], candidate='a')
    with pytest.raises(ValueError, match='Missing'):
        plan.validate_test_files(tmp_path)
    plan.validate_test_files(Path(__file__).resolve().parents[1])


def test_capability_dependency_version_is_validated():
    base = capability(consumers=())
    consumer = capability(id='sample.other', dependencies=(Dependency(Node('capability', 'sample.read'), '2.0.0', '3.0.0'),))
    with pytest.raises(ValueError, match='Incompatible'):
        CapabilityRegistry(components(), [base, consumer])


def test_owner_and_consumer_changes_cannot_produce_empty_success():
    registry = shared_registry()
    owner = regression_plan(registry, [Node('component', 'sample')], candidate='a')
    assert owner.required_consumers == ('other-reader', 'reader')
    consumer = regression_plan(registry, [Node('component', 'other-reader')], candidate='a')
    assert consumer.required_tests == ('tests/test_chief_controls.py',)
    assert consumer.required_consumers == ('other-reader',)
    uncovered = CapabilityRegistry([Component('unused', ComponentKind.SERVICE)], [])
    with pytest.raises(ValueError, match='No required regression coverage'):
        regression_plan(uncovered, [Node('component', 'unused')], candidate='a')


def test_owner_can_consume_own_capability_without_a_false_execution_cycle():
    own = capability(consumers=(Consumer('sample', True, ('tests/test_domains.py',)),))
    registry = CapabilityRegistry(components(), [own])
    assert registry.graph.topological_order()
    plan = regression_plan(registry, [Node('component', 'sample')], candidate='a')
    assert Node('capability', 'sample.read') in plan.affected
    assert plan.required_consumers == ('sample',)
