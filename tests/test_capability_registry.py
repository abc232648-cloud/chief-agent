from dataclasses import FrozenInstanceError, replace

import pytest

from capabilities.contracts import Capability, Component, ComponentKind, Consumer, Dependency, Maturity, Mode, Node
from capabilities.registry import CapabilityRegistry


def components():
    return (Component('sample', ComponentKind.DOMAIN, permissions=('sample.read',)),
            Component('reader', ComponentKind.AGENT, permissions=('sample.read',)))


def capability(**changes):
    value = Capability(
        id='sample.read', owner='sample', version='1.2.0', description='Synthetic read capability.',
        maturity=Maturity.LIMITED, mode=Mode.LEGACY_CONTROLLED, dependencies=(),
        consumers=(Consumer('reader', True, ('tests/test_domains.py',)),), permissions=('sample.read',),
        frameworks=(), data_access=('Sample records only.',), models=(), tests=('tests/test_domains.py',),
        health_dependencies=(), inputs='Scoped request.', outputs='Sample records.', side_effects=(),
        failure_behavior='Deny unauthorized access.', overrides=(), audit=('Read request.',))
    return replace(value, **changes)


def test_duplicate_capabilities_and_components_are_rejected():
    with pytest.raises(ValueError, match='Duplicate capability'):
        CapabilityRegistry(components(), [capability(), capability()])
    with pytest.raises(ValueError, match='Duplicate component'):
        CapabilityRegistry(components() + (components()[0],), [capability()])


@pytest.mark.parametrize('changes,match', [
    ({'owner': 'unknown'}, 'owner'),
    ({'owner': 'reader'}, 'owner'),
    ({'id': 'other.read'}, 'owner namespace'),
    ({'consumers': (Consumer('unknown', True, ('tests/test_domains.py',)),)}, 'consumer'),
    ({'consumers': (Consumer('reader', True, ()),)}, 'Tests'),
    ({'consumers': (Consumer('reader', 'yes', ()),)}, 'consumer'),
    ({'consumers': (Consumer('reader', True, ('tests/test_domains.py',)),) * 2}, 'consumer'),
    ({'permissions': ('other.write',)}, 'owner grants'),
    ({'maturity': 'STABLE'}, 'maturity'),
    ({'mode': 'RUNNING'}, 'mode'),
    ({'tests': ()}, 'Tests'),
    ({'tests': ('tests/../private.py',)}, 'relative'),
    ({'tests': ('/tmp/test_x.py',)}, 'relative'),
    ({'tests': ('tests/test_x.py;rm',)}, 'relative'),
    ({'tests': ['tests/test_domains.py']}, 'immutable'),
    ({'side_effects': []}, 'immutable'),
    ({'health_dependencies': ('unknown',)}, 'health'),
    ({'description': ''}, 'Missing contract'),
    ({'inputs': None}, 'Missing contract'),
    ({'version': '1.2'}, 'version'),
    ({'version': '1.2.0-beta'}, 'version'),
    ({'dependencies': [Dependency(Node('component', 'reader'))]}, 'immutable'),
    ({'dependencies': (Dependency(Node('component', 'absent')), )}, 'Unknown'),
    ({'dependencies': (Dependency(Node('component', 'sample')), ) * 2}, 'duplicate'),
])
def test_invalid_declarations_fail_before_a_registry_is_available(changes, match):
    with pytest.raises(ValueError, match=match):
        CapabilityRegistry(components(), [capability(**changes)])


def test_consumer_permission_mismatch_is_rejected_without_granting_anything():
    owner, reader = components()
    reader = replace(reader, permissions=())
    with pytest.raises(ValueError, match='Consumer lacks'):
        CapabilityRegistry((owner, reader), [capability()])
    assert reader.permissions == ()


@pytest.mark.parametrize('actual,minimum,before,accepted', [
    ('1.0.0', '1.0.0', '2.0.0', True),
    ('1.9.9', '1.0.0', '2.0.0', True),
    ('2.0.0', '1.0.0', '2.0.0', False),
    ('0.9.9', '1.0.0', '2.0.0', False),
])
def test_component_dependency_version_compatibility(actual, minimum, before, accepted):
    dependency = Component('service', ComponentKind.SERVICE, version=actual)
    declaration = capability(dependencies=(Dependency(Node('component', 'service'), minimum, before),))
    if accepted:
        assert CapabilityRegistry(components() + (dependency,), [declaration])
    else:
        with pytest.raises(ValueError, match='Incompatible'):
            CapabilityRegistry(components() + (dependency,), [declaration])


@pytest.mark.parametrize('minimum,before', [('2.0.0', '1.0.0'), ('1.0.0', '1.0.0'), ('bad', '2.0.0')])
def test_invalid_compatibility_intervals_are_not_silently_accepted(minimum, before):
    declaration = capability(dependencies=(Dependency(Node('component', 'sample'), minimum, before),))
    with pytest.raises(ValueError):
        CapabilityRegistry(components(), [declaration])


@pytest.mark.parametrize('maturity', list(Maturity))
@pytest.mark.parametrize('mode', list(Mode))
def test_maturity_and_mode_are_validated_metadata_only(maturity, mode):
    registry = CapabilityRegistry(components(), [capability(maturity=maturity, mode=mode)])
    assert registry.describe()['capabilities'][0]['mode'] == mode.value


def test_registry_snapshot_and_exports_cannot_be_mutated():
    source = list(components())
    registry = CapabilityRegistry(source, [capability()])
    source.clear()
    assert registry.components
    with pytest.raises(TypeError):
        registry.capabilities['sample.read'] = capability()
    with pytest.raises(FrozenInstanceError):
        registry.capabilities['sample.read'].owner = 'other'
    registry.describe()['capabilities'][0]['consumers'].clear()
    assert registry.capabilities['sample.read'].consumers


def test_domain_cannot_declare_another_domains_permission():
    owner, reader = components()
    with pytest.raises(ValueError, match='own namespace'):
        CapabilityRegistry((replace(owner, permissions=('other.read',)), reader), [])


@pytest.mark.parametrize('name', ['sample..read', 'sample.', ' sample.read', '../sample', 'SAMPLE.read'])
def test_noncanonical_node_ids_are_rejected(name):
    with pytest.raises(ValueError, match='Invalid dependency node'):
        Node('capability', name)
