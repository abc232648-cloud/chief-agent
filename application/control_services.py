"""Application composition; generic Core never imports concrete domain providers."""
from dataclasses import dataclass
from capabilities.contracts import Mode, Node
from control.agents import AgentControls
from control.components import ComponentControls
from control.health import SystemHealth
from .composition import default_catalogs


@dataclass(frozen=True)
class ControlServices:
    controls: ComponentControls
    health: SystemHealth


def compose_control_services(store, catalogs=None):
    catalogs = catalogs if catalogs is not None else default_catalogs()
    # Original initialization only; no Checkpoint C DDL or startup/resume here.
    AgentControls(store, catalogs.agents)
    supported = {}
    guarded = set()
    modes = (Mode.ENABLED, Mode.DISABLED, Mode.MAINTENANCE)
    if 'farm-assistant' in catalogs.capabilities.components:
        supported[Node('component', 'farm-assistant')] = modes
        supported[Node('capability', 'farming.assistant.ask')] = modes
        guarded.add(Node('component', 'farm-assistant'))
    for domain in catalogs.agents.domains:
        _, agent = catalogs.agents.resolve(domain)
        node = Node('component', agent.id)
        supported[node] = modes
        guarded.add(node)
        for capability in agent.capabilities:
            supported[Node('capability', capability)] = modes
    controls = ComponentControls(store, catalogs.capabilities, supported=supported, guarded_consumers=guarded)
    return ControlServices(controls, SystemHealth(controls))
