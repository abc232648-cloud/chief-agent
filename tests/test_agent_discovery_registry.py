from dataclasses import replace

import pytest

from agents.discovery import ManifestRegistry
from agents.manifest import AgentManifest, CompatibilityDeclaration
from agents.registry import AgentRegistry
from application.composition import compose_catalogs, default_catalogs, default_manifest_registry
from domains.contracts import AgentDefinition, DomainDefinition
from domains.jobs import definition as jobs
from domains.jobs.capabilities import components as job_components, definitions as job_capabilities
from domains.jobs.manifest import manifest as job_manifest
from domains.farming.manifest import manifest as farm_manifest


def test_default_discovery_registry_has_complete_installed_job_and_farm_manifests():
    catalogs = default_catalogs()
    registry = catalogs.manifests
    assert registry is not None
    assert registry.ids() == ('jobs', 'farming')
    assert registry.runtime_agent_id('jobs') == 'jobs-worker'
    assert registry.runtime_agent_id('farming') == 'farming-recorder'
    assert registry.get('jobs') == job_manifest()
    assert registry.get('farming') == farm_manifest()


def test_default_discovery_registry_filters_declared_interfaces_without_granting_authority():
    registry = default_manifest_registry()
    assert [manifest.id for manifest in registry.for_interface('owner')] == ['jobs', 'farming']
    assert [manifest.id for manifest in registry.for_interface('staff')] == ['farming']
    assert [manifest.id for manifest in registry.for_interface('companion')] == ['jobs']
    assert registry.get('farming').interfaces[1].roles == ('Manager', 'Supervisor', 'Worker')
    assert registry.get('jobs').interfaces[1].roles == ('Owner', 'Administrator')


def test_discovery_description_is_deterministic_and_links_to_runtime_agent_only_as_metadata():
    description = default_manifest_registry().describe()
    assert [item['id'] for item in description] == ['jobs', 'farming']
    assert description[0]['runtime_agent_id'] == 'jobs-worker'
    assert description[1]['runtime_agent_id'] == 'farming-recorder'
    assert description[0]['interfaces'][0] == {
        'kind': 'owner',
        'module': 'jobs',
        'roles': ['Owner', 'Administrator'],
    }
    assert description[1]['interfaces'][1] == {
        'kind': 'staff',
        'module': 'farming',
        'roles': ['Manager', 'Supervisor', 'Worker'],
    }


def test_registry_rejects_missing_runtime_domain_duplicate_and_runtime_drift():
    runtime = AgentRegistry()
    domain, agent = jobs()
    runtime.register(domain, agent)
    registry = ManifestRegistry(runtime)
    registry.register(job_manifest())
    with pytest.raises(ValueError, match='Duplicate agent manifest registration'):
        registry.register(job_manifest())

    unknown = AgentManifest(
        id='demo',
        name='Demo',
        version='1.0.0',
        description='Synthetic demo.',
        requires=CompatibilityDeclaration(chief='>=1.0.0 <2.0.0'),
        capabilities=('demo.execute',),
    )
    with pytest.raises(ValueError, match='no installed runtime domain'):
        registry.register(unknown)

    fresh = ManifestRegistry(runtime)
    with pytest.raises(ValueError, match='exactly match runtime agent grants'):
        fresh.register(replace(job_manifest(), capabilities=()))


def test_complete_registry_fails_closed_when_an_installed_domain_has_no_manifest():
    runtime = AgentRegistry()
    runtime.register(*jobs())
    runtime.register(
        DomainDefinition('demo', 'Demo', 'Synthetic demo.'),
        AgentDefinition('demo-worker', 'demo', frozenset()),
    )
    registry = ManifestRegistry(runtime)
    registry.register(job_manifest())
    with pytest.raises(ValueError, match='Installed runtime domains require manifests: demo'):
        registry.require_complete()


def test_runtime_only_custom_composition_remains_supported_but_can_opt_into_coverage_gate():
    domain, agent = jobs()
    provider = (domain, agent, job_capabilities(), job_components())
    catalogs = compose_catalogs((provider,))
    assert catalogs.agents.resolve('jobs') == (domain, agent)
    assert catalogs.manifests is not None
    assert len(catalogs.manifests) == 0

    with pytest.raises(ValueError, match='Installed runtime domains require manifests: jobs'):
        compose_catalogs((provider,), require_manifest_coverage=True)

    complete = compose_catalogs(
        (provider,),
        manifests=(job_manifest(),),
        require_manifest_coverage=True,
    )
    assert complete.manifests is not None
    assert complete.manifests.ids() == ('jobs',)


def test_registry_instances_are_fresh_and_unknown_lookups_fail_closed():
    first = default_manifest_registry()
    second = default_manifest_registry()
    assert first is not second
    assert first.describe() == second.describe()
    with pytest.raises(ValueError, match='Unknown agent manifest'):
        first.get('security')
    with pytest.raises(ValueError, match='Unsupported agent interface'):
        first.for_interface('desktop')
