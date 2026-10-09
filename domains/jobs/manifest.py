"""Declarative Job Agent manifest for Chief discovery surfaces."""
from agents.manifest import AgentManifest, CompatibilityDeclaration, InterfaceDeclaration


def manifest() -> AgentManifest:
    return AgentManifest(
        id='jobs',
        name='Job Agent',
        version='1.0.0',
        description='Job research, candidate evidence and reviewed applications.',
        requires=CompatibilityDeclaration(
            chief='>=1.0.0 <2.0.0',
            owner_api='1',
            companion_api='1',
        ),
        capabilities=('jobs.execute',),
        interfaces=(
            InterfaceDeclaration('owner', 'jobs', ('Owner', 'Administrator')),
            InterfaceDeclaration('companion', 'jobs', ('Owner', 'Administrator')),
        ),
        notifications=True,
    )
