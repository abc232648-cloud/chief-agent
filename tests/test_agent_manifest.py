from dataclasses import replace

import pytest

from agents.manifest import (
    AgentManifest,
    CompatibilityDeclaration,
    InterfaceDeclaration,
    interface,
    validate_against_runtime,
    validate_unique_manifests,
)
from domains.farming import definition as farming
from domains.jobs import definition as jobs


def manifest_for(factory, *, interfaces=(), notifications=True):
    domain, agent = factory()
    manifest = AgentManifest(
        id=domain.id,
        name=domain.label,
        version='1.0.0',
        description=domain.description,
        requires=CompatibilityDeclaration(chief='>=1 <2', owner_api='1'),
        capabilities=tuple(agent.capabilities),
        interfaces=interfaces,
        notifications=notifications,
    )
    return manifest, domain, agent


def test_job_manifest_supports_owner_and_optional_companion_without_granting_authority():
    manifest, domain, agent = manifest_for(
        jobs,
        interfaces=(
            InterfaceDeclaration('owner', 'jobs', ('Owner', 'Administrator')),
            InterfaceDeclaration('companion', 'jobs', ('Owner', 'Administrator')),
        ),
    )
    assert validate_against_runtime(manifest, domain, agent) is manifest
    assert interface(manifest, 'owner').module == 'jobs'
    assert interface(manifest, 'staff') is None
    assert manifest.as_dict()['interfaces'][1] == {
        'kind': 'companion',
        'module': 'jobs',
        'roles': ['Owner', 'Administrator'],
    }


def test_farm_manifest_supports_owner_and_staff_role_audiences():
    manifest, domain, agent = manifest_for(
        farming,
        interfaces=(
            InterfaceDeclaration('owner', 'farming', ('Owner', 'Administrator')),
            InterfaceDeclaration('staff', 'farming', ('Manager', 'Supervisor', 'Worker')),
        ),
    )
    assert validate_against_runtime(manifest, domain, agent) is manifest
    assert interface(manifest, 'staff').roles == ('Manager', 'Supervisor', 'Worker')
    assert manifest.as_dict()['capabilities'] == sorted(agent.capabilities)


def test_manifest_rejects_invalid_schema_identifiers_versions_and_interfaces():
    base, _, _ = manifest_for(jobs)
    with pytest.raises(ValueError, match='schema version'):
        replace(base, schema_version=99)
    with pytest.raises(ValueError, match='stable identifier'):
        replace(base, id='Jobs Agent')
    with pytest.raises(ValueError, match='semantic versioning'):
        replace(base, version='v1')
    with pytest.raises(ValueError, match='Unsupported agent interface'):
        InterfaceDeclaration('desktop', 'jobs')
    with pytest.raises(ValueError, match='Interface module'):
        InterfaceDeclaration('owner', '/jobs/')
    with pytest.raises(ValueError, match='roles must be unique'):
        InterfaceDeclaration('staff', 'farming', ('Worker', 'Worker'))


def test_manifest_rejects_capability_and_interface_ambiguity():
    base, _, _ = manifest_for(jobs)
    with pytest.raises(ValueError, match='capabilities must be unique'):
        replace(base, capabilities=('jobs.execute', 'jobs.execute'))
    with pytest.raises(ValueError, match='namespace'):
        replace(base, capabilities=('farming.records.read',))
    with pytest.raises(ValueError, match='each interface kind only once'):
        replace(
            base,
            interfaces=(InterfaceDeclaration('owner', 'jobs'), InterfaceDeclaration('owner', 'jobs-alt')),
        )
    with pytest.raises(ValueError, match='boolean'):
        replace(base, notifications='yes')


def test_manifest_requires_explicit_nonempty_compatibility():
    with pytest.raises(TypeError):
        AgentManifest(id='demo', name='Demo', version='1.0.0', description='Demo agent.')
    with pytest.raises(ValueError, match='Chief compatibility'):
        CompatibilityDeclaration(chief='   ')
    with pytest.raises(ValueError, match='explicit CompatibilityDeclaration'):
        AgentManifest(
            id='demo',
            name='Demo',
            version='1.0.0',
            description='Demo agent.',
            requires=None,
        )


def test_runtime_alignment_fails_closed_on_metadata_or_grant_drift():
    manifest, domain, agent = manifest_for(jobs)
    assert validate_against_runtime(manifest, domain, agent) is manifest
    wrong_domain = AgentManifest(
        id='other',
        name=manifest.name,
        version=manifest.version,
        description=manifest.description,
        requires=manifest.requires,
        capabilities=(),
    )
    with pytest.raises(ValueError, match='same runtime domain'):
        validate_against_runtime(wrong_domain, domain, agent)
    with pytest.raises(ValueError, match='name must match'):
        validate_against_runtime(replace(manifest, name='Renamed'), domain, agent)
    with pytest.raises(ValueError, match='description must match'):
        validate_against_runtime(replace(manifest, description='Different'), domain, agent)
    with pytest.raises(ValueError, match='exactly match runtime agent grants'):
        validate_against_runtime(replace(manifest, capabilities=()), domain, agent)


def test_manifest_set_rejects_duplicates_and_unvalidated_values():
    jobs_manifest, _, _ = manifest_for(jobs)
    farm_manifest, _, _ = manifest_for(farming)
    assert validate_unique_manifests((jobs_manifest, farm_manifest)) == (jobs_manifest, farm_manifest)
    with pytest.raises(ValueError, match='Duplicate agent manifest id'):
        validate_unique_manifests((jobs_manifest, jobs_manifest))
    with pytest.raises(ValueError, match='Only validated AgentManifest'):
        validate_unique_manifests((jobs_manifest, object()))


def test_interface_lookup_rejects_unknown_interface_kind():
    manifest, _, _ = manifest_for(jobs)
    with pytest.raises(ValueError, match='Unsupported agent interface'):
        interface(manifest, 'desktop')
