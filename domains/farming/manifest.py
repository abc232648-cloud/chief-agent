"""Declarative Farm Agent manifest for Chief discovery surfaces."""
from agents.manifest import AgentManifest, CompatibilityDeclaration, InterfaceDeclaration


def manifest() -> AgentManifest:
    return AgentManifest(
        id='farming',
        name='Farm Agent',
        version='1.0.0',
        description='Farm records and reminders, with an isolated poultry-record pilot. Equipment automation is not enabled.',
        requires=CompatibilityDeclaration(
            chief='>=1.0.0 <2.0.0',
            owner_api='1',
            staff_api='1',
        ),
        capabilities=(
            'farming.records.read',
            'farming.records.write',
            'farming.reminders.write',
        ),
        interfaces=(
            InterfaceDeclaration('owner', 'farming', ('Owner', 'Administrator')),
            InterfaceDeclaration('staff', 'farming', ('Manager', 'Supervisor', 'Worker')),
        ),
        notifications=True,
    )
