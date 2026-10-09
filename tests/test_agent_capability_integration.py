import pytest

from agents.discovery import ManifestRegistry
from agents.manifest import AgentManifest, CompatibilityDeclaration
from agents.registry import AgentRegistry
from application.composition import default_catalogs
from capabilities.contracts import Component, ComponentKind
from capabilities.registry import CapabilityRegistry
from domains.contracts import AgentDefinition, DomainDefinition


def _demo_manifest(*capabilities: str) -> AgentManifest:
    return AgentManifest(
        id='demo',
        name='Demo',
        version='1.0.0',
        description='Synthetic demo.',
        requires=CompatibilityDeclaration(chief='>=1.0.0 <2.0.0'),
        capabilities=capabilities,
    )


def test_default_manifests_are_bound_to_the_same_capability_snapshot_as_application_catalogs():
    catalogs = default_catalogs()
    manifests = catalogs.manifests
    assert manifests is not None
    assert manifests.capabilities is catalogs.capabilities
    assert manifests.require_capability_alignment() is manifests

    for agent_id in manifests.ids():
        manifest = manifests.get(agent_id)
        declarations = catalogs.capabilities.require_domain_capabilities(
            agent_id,
            manifest.capabilities,
        )
        assert tuple(capability.id for capability in declarations) == manifest.capabilities
        assert all(capability.owner == agent_id for capability in declarations)


def test_capability_registry_rejects_unknown_foreign_and_incomplete_domain_sets():
    capabilities = default_catalogs().capabilities

    with pytest.raises(ValueError, match='Unknown capability domain: security'):
        capabilities.require_domain_capabilities('security', ())

    with pytest.raises(ValueError, match='Undeclared capabilities: jobs.missing'):
        capabilities.require_domain_capabilities('jobs', ('jobs.missing',))

    with pytest.raises(ValueError, match='Capabilities owned by another component: jobs.execute'):
        capabilities.require_domain_capabilities('farming', ('jobs.execute',))

    with pytest.raises(ValueError, match='exactly match domain component grants'):
        capabilities.require_domain_capabilities('farming', ('farming.records.read',))


def test_manifest_registration_fails_when_runtime_grant_has_no_capability_declaration():
    runtime = AgentRegistry()
    runtime.register(
        DomainDefinition('demo', 'Demo', 'Synthetic demo.'),
        AgentDefinition('demo-worker', 'demo', frozenset({'demo.execute'})),
    )
    capability_catalog = CapabilityRegistry(
        (Component('demo', ComponentKind.DOMAIN, permissions=('demo.execute',)),),
        (),
    )
    manifests = ManifestRegistry(runtime, capability_catalog)

    with pytest.raises(ValueError, match='Undeclared capabilities: demo.execute'):
        manifests.register(_demo_manifest('demo.execute'))


def test_runtime_only_manifest_registry_is_explicitly_non_authoritative_for_capabilities():
    runtime = AgentRegistry()
    runtime.register(
        DomainDefinition('demo', 'Demo', 'Synthetic demo.'),
        AgentDefinition('demo-worker', 'demo', frozenset()),
    )
    manifests = ManifestRegistry(runtime)
    manifests.register(_demo_manifest())

    with pytest.raises(ValueError, match='require capability-registry alignment'):
        manifests.require_capability_alignment()
