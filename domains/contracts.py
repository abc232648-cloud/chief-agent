from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class DomainAction:
    name: str
    label: str
    capability: str
    fields: tuple[dict, ...]
    execute: Callable


@dataclass(frozen=True)
class DomainDefinition:
    id: str
    label: str
    description: str
    actions: tuple[DomainAction, ...] = ()
    processor_factory: Callable | None = None
    pages: tuple[tuple[str,str], ...] = ()


@dataclass(frozen=True)
class AgentDefinition:
    id: str
    domain: str
    capabilities: frozenset[str] = field(default_factory=frozenset)
