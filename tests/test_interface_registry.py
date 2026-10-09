import pytest

from agents.discovery import ManifestRegistry
from agents.interfaces import InterfaceRegistry
from agents.manifest import AgentManifest, CompatibilityDeclaration, InterfaceDeclaration
from agents.registry import AgentRegistry
from application.composition import compose_catalogs, default_catalogs, default_interface_registry
from capabilities.contracts import Component, ComponentKind
from capabilities.registry import CapabilityRegistry
from domains.contracts import AgentDefinition, DomainDefinition


def _manifest(domain_id: str, label: str, description: str, *, module: str, kind: str = 'owner'):
    return AgentManifest(
        id=domain_id,
        name=label,
        version='1.0.0',
        description=description,
        requires=CompatibilityDeclaration(chief='>=1.0.0 <2.0.0'),
        capabilities=(),
        interfaces=(InterfaceDeclaration(kind, module, ('Owner',)),),
        notifications=False,
    )


def _aligned_manifests(runtime: AgentRegistry) -> ManifestRegistry:
    components = []
    for domain_id in runtime.domains:
        agents = [agent for agent in runtime.agents.values() if agent.domain == domain_id]
        permissions = tuple(sorted(agents[0].capabilities)) if len(agents) == 1 else ()
        components.append(Component(domain_id, ComponentKind.DOMAIN, permissions=permissions))
    return ManifestRegistry(runtime, CapabilityRegistry(tuple(components), ()))


def test_default_interface_registry_describes_job_and_farm_availability_deterministically():
    registry = default_interface_registry()
    assert registry.describe() == [
        {
            'id': 'jobs',
            'name': 'Job Agent',
            'version': '1.0.0',
            'notifications': True,
            'interfaces': {
                'owner': {
                    'available': True,
                    'module': 'jobs',
                    'roles': ['Owner', 'Administrator'],
                },
                'staff': {'available': False},
                'companion': {
                    'available': True,
                    'module': 'jobs',
                    'roles': ['Owner', 'Administrator'],
                },
            },
        },
        {
            'id': 'farming',
            'name': 'Farm Agent',
            'version': '1.0.0',
            'notifications': True,
            'interfaces': {
                'owner': {
                    'available': True,
                    'module': 'farming',
                    'roles': ['Owner', 'Administrator'],
                },
                'staff': {
                    'available': True,
                    'module': 'farming',
                    'roles': ['Manager', 'Supervisor', 'Worker'],
                },
                'companion': {'available': False},
            },
        },
    ]


def test_interface_registry_availability_and_required_lookup_are_fail_closed():
    registry = default_interface_registry()
    assert registry.available('jobs', 'owner') is True
    assert registry.available('jobs', 'staff') is False
    assert registry.require('jobs', 'companion').module == 'jobs'
    assert registry.require('farming', 'staff').roles == ('Manager', 'Supervisor', 'Worker')
    with pytest.raises(ValueError, match='does not expose'):
        registry.require('jobs', 'staff')
    with pytest.raises(ValueError, match='Unknown agent manifest'):
        registry.available('security', 'owner')
    with pytest.raises(ValueError, match='Unsupported agent interface'):
        registry.available('jobs', 'desktop')


def test_surface_and_role_filters_are_presentation_metadata_only():
    registry = default_interface_registry()
    assert [(item.agent_id, item.module) for item in registry.for_kind('owner')] == [
        ('jobs', 'jobs'),
        ('farming', 'farming'),
    ]
    assert [item.agent_id for item in registry.for_kind('staff')] == ['farming']
    assert [item.agent_id for item in registry.for_kind('companion')] == ['jobs']
    assert [item.agent_id for item in registry.for_role('staff', 'Manager')] == ['farming']
    assert [item.agent_id for item in registry.for_role('staff', 'Worker')] == ['farming']
    assert registry.for_role('staff', 'Owner') == ()
    with pytest.raises(ValueError, match='Interface role must be non-empty'):
        registry.for_role('staff', '   ')


def test_notifications_are_declarations_not_interface_or_lifecycle_authority():
    registry = default_interface_registry()
    assert registry.notifications_declared('jobs') is True
    assert registry.notifications_declared('farming') is True
    for agent in registry.describe():
        assert 'enabled' not in agent
        assert all('enabled' not in surface for surface in agent['interfaces'].values())


def test_interface_registry_requires_complete_manifest_coverage():
    runtime = AgentRegistry()
    runtime.register(DomainDefinition('one', 'One', 'First.'), AgentDefinition('one-worker', 'one', frozenset()))
    runtime.register(DomainDefinition('two', 'Two', 'Second.'), AgentDefinition('two-worker', 'two', frozenset()))
    manifests = _aligned_manifests(runtime)
    manifests.register(_manifest('one', 'One', 'First.', module='one'))
    with pytest.raises(ValueError, match='Installed runtime domains require manifests: two'):
        InterfaceRegistry(manifests)


def test_interface_registry_requires_capability_aligned_manifest_registry():
    runtime = AgentRegistry()
    runtime.register(DomainDefinition('one', 'One', 'First.'), AgentDefinition('one-worker', 'one', frozenset()))
    manifests = ManifestRegistry(runtime)
    manifests.register(_manifest('one', 'One', 'First.', module='one'))
    with pytest.raises(ValueError, match='require capability-registry alignment'):
        InterfaceRegistry(manifests)


def test_interface_registry_rejects_same_surface_module_collision_across_agents():
    runtime = AgentRegistry()
    runtime.register(DomainDefinition('one', 'One', 'First.'), AgentDefinition('one-worker', 'one', frozenset()))
    runtime.register(DomainDefinition('two', 'Two', 'Second.'), AgentDefinition('two-worker', 'two', frozenset()))
    manifests = _aligned_manifests(runtime)
    manifests.register(_manifest('one', 'One', 'First.', module='shared'))
    manifests.register(_manifest('two', 'Two', 'Second.', module='shared'))
    with pytest.raises(ValueError, match='Interface module collision for owner:shared'):
        InterfaceRegistry(manifests)


def test_composition_only_builds_authoritative_interface_registry_with_complete_coverage():
    domain = DomainDefinition('demo', 'Demo', 'Synthetic demo.')
    agent = AgentDefinition('demo-worker', 'demo', frozenset())
    provider = (domain, agent, (), ())

    partial = compose_catalogs((provider,))
    assert partial.manifests is not None
    assert partial.interfaces is None

    complete = compose_catalogs(
        (provider,),
        manifests=(_manifest('demo', 'Demo', 'Synthetic demo.', module='demo'),),
        require_manifest_coverage=True,
    )
    assert complete.interfaces is not None
    assert complete.interfaces.require('demo', 'owner').module == 'demo'
    assert complete.interfaces.describe_agent('demo')['interfaces']['staff'] == {'available': False}


def test_default_catalogs_exposes_fresh_interface_registries():
    first = default_catalogs().interfaces
    second = default_catalogs().interfaces
    assert first is not None and second is not None
    assert first is not second
    assert first.describe() == second.describe()
