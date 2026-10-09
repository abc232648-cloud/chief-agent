"""Declarative agent manifest contract used by Chief interfaces and registries.

The runtime DomainDefinition/AgentDefinition pair remains authoritative for execution.
This module describes installable UI/notification metadata and validates that it does
not drift from the runtime definition it represents.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable

from domains.contracts import AgentDefinition, DomainDefinition

MANIFEST_SCHEMA_VERSION = 1
INTERFACE_KINDS = frozenset({'owner', 'staff', 'companion'})
_TOKEN_RE = re.compile(r'^[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*$')
_SEMVER_RE = re.compile(r'^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$')


def _text(value: str, label: str, *, limit: int = 240) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f'{label} must contain 1-{limit} characters.')
    return value.strip()


def _token(value: str, label: str) -> str:
    value = _text(value, label, limit=80)
    if not _TOKEN_RE.fullmatch(value):
        raise ValueError(f'{label} must be a lowercase stable identifier.')
    return value


@dataclass(frozen=True)
class InterfaceDeclaration:
    """One UI surface that an agent can expose.

    ``roles`` declares intended presentation audiences only. Server-side Chief
    authorization remains authoritative for every request.
    """

    kind: str
    module: str
    roles: tuple[str, ...] = ()

    def __post_init__(self):
        if self.kind not in INTERFACE_KINDS:
            raise ValueError(f'Unsupported agent interface: {self.kind!r}.')
        object.__setattr__(self, 'module', _token(self.module, 'Interface module'))
        normalized = tuple(_text(role, 'Interface role', limit=80) for role in self.roles)
        if len(normalized) != len(set(normalized)):
            raise ValueError('Interface roles must be unique.')
        object.__setattr__(self, 'roles', normalized)

    def as_dict(self):
        return {'kind': self.kind, 'module': self.module, 'roles': list(self.roles)}


@dataclass(frozen=True)
class CompatibilityDeclaration:
    """Declared compatibility requirements; enforcement is a later registry phase."""

    chief: str
    owner_api: str | None = None
    staff_api: str | None = None
    companion_api: str | None = None

    def __post_init__(self):
        object.__setattr__(self, 'chief', _text(self.chief, 'Chief compatibility', limit=80))
        for name in ('owner_api', 'staff_api', 'companion_api'):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name.replace('_', ' ').title(), limit=40))

    def as_dict(self):
        result = {'chief': self.chief}
        for name in ('owner_api', 'staff_api', 'companion_api'):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        return result


@dataclass(frozen=True)
class AgentManifest:
    """Validated declarative metadata for one Chief agent/domain."""

    id: str
    name: str
    version: str
    description: str
    capabilities: tuple[str, ...] = ()
    interfaces: tuple[InterfaceDeclaration, ...] = ()
    notifications: bool = False
    requires: CompatibilityDeclaration = field(default_factory=lambda: CompatibilityDeclaration('unspecified'))
    schema_version: int = MANIFEST_SCHEMA_VERSION

    def __post_init__(self):
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ValueError(f'Unsupported agent manifest schema version: {self.schema_version!r}.')
        object.__setattr__(self, 'id', _token(self.id, 'Agent manifest id'))
        object.__setattr__(self, 'name', _text(self.name, 'Agent manifest name', limit=120))
        object.__setattr__(self, 'description', _text(self.description, 'Agent manifest description', limit=500))
        if not isinstance(self.version, str) or not _SEMVER_RE.fullmatch(self.version):
            raise ValueError('Agent manifest version must use semantic versioning (for example 1.0.0).')
        capabilities = tuple(self.capabilities)
        if len(capabilities) != len(set(capabilities)):
            raise ValueError('Agent manifest capabilities must be unique.')
        for capability in capabilities:
            if not isinstance(capability, str) or not capability.startswith(self.id + '.'):
                raise ValueError('Agent manifest capabilities must belong to the manifest id namespace.')
        object.__setattr__(self, 'capabilities', tuple(sorted(capabilities)))
        interfaces = tuple(self.interfaces)
        kinds = [interface.kind for interface in interfaces]
        if len(kinds) != len(set(kinds)):
            raise ValueError('An agent manifest may declare each interface kind only once.')
        object.__setattr__(self, 'interfaces', interfaces)
        if not isinstance(self.notifications, bool):
            raise ValueError('Agent manifest notifications must be boolean.')
        if not isinstance(self.requires, CompatibilityDeclaration):
            raise ValueError('Agent manifest requires must be a CompatibilityDeclaration.')

    def as_dict(self):
        return {
            'schema_version': self.schema_version,
            'id': self.id,
            'name': self.name,
            'version': self.version,
            'description': self.description,
            'capabilities': list(self.capabilities),
            'interfaces': [interface.as_dict() for interface in self.interfaces],
            'notifications': self.notifications,
            'requires': self.requires.as_dict(),
        }


def validate_against_runtime(manifest: AgentManifest, domain: DomainDefinition, agent: AgentDefinition) -> AgentManifest:
    """Fail closed when presentation metadata drifts from runtime authority."""

    if not isinstance(manifest, AgentManifest):
        raise ValueError('A validated AgentManifest is required.')
    if manifest.id != domain.id or agent.domain != domain.id:
        raise ValueError('Agent manifest must identify the same runtime domain.')
    if manifest.name != domain.label:
        raise ValueError('Agent manifest name must match the runtime domain label.')
    if manifest.description != domain.description:
        raise ValueError('Agent manifest description must match the runtime domain description.')
    if set(manifest.capabilities) != set(agent.capabilities):
        raise ValueError('Agent manifest capabilities must exactly match runtime agent grants.')
    return manifest


def interface(manifest: AgentManifest, kind: str) -> InterfaceDeclaration | None:
    """Return one declared interface without granting authority."""

    if kind not in INTERFACE_KINDS:
        raise ValueError(f'Unsupported agent interface: {kind!r}.')
    return next((item for item in manifest.interfaces if item.kind == kind), None)


def validate_unique_manifests(manifests: Iterable[AgentManifest]) -> tuple[AgentManifest, ...]:
    manifests = tuple(manifests)
    ids = [manifest.id for manifest in manifests]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate agent manifest id.')
    return manifests
