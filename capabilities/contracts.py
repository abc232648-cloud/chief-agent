"""Immutable declarations for versioned capabilities and their consumers."""
from dataclasses import dataclass
from enum import Enum
import re


class Maturity(str, Enum):
    EXPERIMENTAL = 'EXPERIMENTAL'
    SHADOW_READY = 'SHADOW_READY'
    LIMITED = 'LIMITED'
    STABLE = 'STABLE'
    CRITICAL = 'CRITICAL'
    DEPRECATED = 'DEPRECATED'


class Mode(str, Enum):
    LEGACY_CONTROLLED = 'LEGACY_CONTROLLED'
    ENABLED = 'ENABLED'
    DISABLED = 'DISABLED'
    SHADOW = 'SHADOW'
    MAINTENANCE = 'MAINTENANCE'


class ComponentKind(str, Enum):
    CORE = 'CORE'
    DOMAIN = 'DOMAIN'
    AGENT = 'AGENT'
    SERVICE = 'SERVICE'


def version(value):
    """Contract versions use strict major.minor.patch; prereleases are unsupported."""
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value):
        raise ValueError('Use a major.minor.patch contract version.')
    return tuple(int(part) for part in value.split('.'))


@dataclass(frozen=True, order=True)
class Node:
    kind: str
    id: str

    def __post_init__(self):
        if self.kind not in ('capability', 'component') or not isinstance(self.id, str) or not re.fullmatch(r'[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*', self.id):
            raise ValueError('Invalid dependency node.')

    @property
    def key(self):
        return self.kind + ':' + self.id


@dataclass(frozen=True)
class Dependency:
    node: Node
    minimum: str = '1.0.0'
    before: str = '2.0.0'

    def accepts(self, target_version):
        lower, upper = version(self.minimum), version(self.before)
        if lower >= upper:
            raise ValueError('Dependency version interval must be nonempty.')
        return lower <= version(target_version) < upper


@dataclass(frozen=True)
class Consumer:
    component: str
    required: bool
    tests: tuple[str, ...]


@dataclass(frozen=True)
class Component:
    id: str
    kind: ComponentKind
    version: str = '1.0.0'
    dependencies: tuple[Dependency, ...] = ()
    tests: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Capability:
    id: str
    owner: str
    version: str
    description: str
    maturity: Maturity
    mode: Mode
    dependencies: tuple[Dependency, ...]
    consumers: tuple[Consumer, ...]
    permissions: tuple[str, ...]
    frameworks: tuple[str, ...]
    data_access: tuple[str, ...]
    models: tuple[str, ...]
    tests: tuple[str, ...]
    health_dependencies: tuple[str, ...]
    inputs: str
    outputs: str
    side_effects: tuple[str, ...]
    failure_behavior: str
    overrides: tuple[str, ...]
    audit: tuple[str, ...]
