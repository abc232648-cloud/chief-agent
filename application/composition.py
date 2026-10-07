"""Compose the installed domains outside Chief Core.

Each caller receives a fresh registry. Domain factories and metadata are unchanged.
"""
from agents.registry import AgentRegistry
from dataclasses import dataclass
from capabilities.contracts import Component, ComponentKind
from capabilities.registry import CapabilityRegistry


@dataclass(frozen=True)
class ApplicationCatalogs:
    agents: AgentRegistry
    capabilities: CapabilityRegistry


def compose_catalogs(providers, *, shared_components=(), shared_capabilities=()):
    """Providers supply domain registration, capability definitions and components.

    Nothing in Core imports providers. No capability metadata is attached to or
    substituted for AgentRegistry. Runtime authorization still uses its grants.
    """
    agents = AgentRegistry()
    components = list(shared_components)
    capabilities = list(shared_capabilities)
    for domain, agent, declarations, services in providers:
        agents.register(domain, agent)
        declarations = tuple(declarations)
        declared = {capability.id for capability in declarations}
        if declared != set(agent.capabilities) or any(c.owner != domain.id for c in declarations):
            raise ValueError('Domain capability declarations must exactly describe existing agent grants.')
        if any(action.capability not in declared for action in domain.actions):
            raise ValueError('Domain action has no capability declaration.')
        components.extend((Component(domain.id, ComponentKind.DOMAIN, permissions=tuple(sorted(agent.capabilities))),
                           Component(agent.id, ComponentKind.AGENT, permissions=tuple(sorted(agent.capabilities)))))
        components.extend(services)
        capabilities.extend(declarations)
    return ApplicationCatalogs(agents, CapabilityRegistry(components, capabilities))


def default_catalogs():
    from domains.jobs import definition as jobs
    from domains.farming import definition as farming
    from domains.jobs.capabilities import definitions as job_capabilities, components as job_components
    from domains.farming.capabilities import definitions as farm_capabilities
    from .legacy_capabilities import shared_components, shared_capabilities
    from .evidence_capabilities import components as evidence_components, capabilities as evidence_capabilities
    from .execution_capabilities import components as execution_components, capabilities as execution_capabilities
    from .checkpoint_g_capabilities import components as g_components, capabilities as g_capabilities
    from .farm_assistant_capabilities import components as assistant_components, capabilities as assistant_capabilities
    from .report_capabilities import components as report_components, capabilities as report_capabilities
    providers = ((*jobs(), job_capabilities(), job_components()),
                 (*farming(), farm_capabilities(), ()))
    return compose_catalogs(
        providers,
        shared_components=shared_components()+evidence_components()+execution_components()+g_components()+assistant_components()+report_components(),
        shared_capabilities=shared_capabilities()+evidence_capabilities()+execution_capabilities()+g_capabilities()+assistant_capabilities()+report_capabilities(),
    )


def default_registry():
    """Compatibility entry point: return only the unchanged domain/agent catalog."""
    return default_catalogs().agents
