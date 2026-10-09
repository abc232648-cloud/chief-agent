"""Typed interface discovery derived from validated Chief agent manifests.

This registry answers which presentation surfaces are available for installed agents.
It never grants runtime authority and does not model enabled/disabled lifecycle state.
"""
from __future__ import annotations

from dataclasses import dataclass

from agents.discovery import ManifestRegistry
from agents.manifest import INTERFACE_KINDS, AgentManifest, InterfaceDeclaration

INTERFACE_KIND_ORDER = ('owner', 'staff', 'companion')


@dataclass(frozen=True)
class InterfaceRegistration:
    """One validated presentation surface contributed by an installed agent."""

    agent_id: str
    kind: str
    module: str
    roles: tuple[str, ...]

    @classmethod
    def from_manifest(cls, manifest: AgentManifest, declaration: InterfaceDeclaration) -> 'InterfaceRegistration':
        if declaration.kind not in INTERFACE_KINDS:
            raise ValueError(f'Unsupported agent interface: {declaration.kind!r}.')
        return cls(manifest.id, declaration.kind, declaration.module, declaration.roles)

    def as_dict(self) -> dict:
        return {
            'agent_id': self.agent_id,
            'kind': self.kind,
            'module': self.module,
            'roles': list(self.roles),
        }


class InterfaceRegistry:
    """Authoritative catalog of *available* presentation interfaces.

    Availability means the installed, runtime-aligned manifest declares the surface.
    It does not mean the agent/interface is enabled, healthy, or authorized for the
    current principal. Lifecycle and authorization remain separate Chief concerns.
    """

    def __init__(self, manifests: ManifestRegistry):
        if not isinstance(manifests, ManifestRegistry):
            raise TypeError('InterfaceRegistry requires a ManifestRegistry.')
        manifests.require_complete()
        self.manifests = manifests
        self._entries: dict[tuple[str, str], InterfaceRegistration] = {}
        self._modules: dict[tuple[str, str], str] = {}
        self._notifications: dict[str, bool] = {}
        for agent_id in manifests.ids():
            manifest = manifests.get(agent_id)
            self._notifications[agent_id] = manifest.notifications
            for declaration in manifest.interfaces:
                registration = InterfaceRegistration.from_manifest(manifest, declaration)
                key = (agent_id, registration.kind)
                if key in self._entries:
                    raise ValueError('Duplicate interface registration.')
                module_key = (registration.kind, registration.module)
                owner = self._modules.get(module_key)
                if owner is not None and owner != agent_id:
                    raise ValueError(
                        f'Interface module collision for {registration.kind}:{registration.module}.'
                    )
                self._entries[key] = registration
                self._modules[module_key] = agent_id

    def _validate_kind(self, kind: str) -> str:
        if kind not in INTERFACE_KINDS:
            raise ValueError(f'Unsupported agent interface: {kind!r}.')
        return kind

    def _validate_agent(self, agent_id: str) -> str:
        self.manifests.get(agent_id)
        return agent_id

    def get(self, agent_id: str, kind: str) -> InterfaceRegistration | None:
        self._validate_agent(agent_id)
        self._validate_kind(kind)
        return self._entries.get((agent_id, kind))

    def require(self, agent_id: str, kind: str) -> InterfaceRegistration:
        registration = self.get(agent_id, kind)
        if registration is None:
            raise ValueError(f'Agent {agent_id!r} does not expose a {kind!r} interface.')
        return registration

    def available(self, agent_id: str, kind: str) -> bool:
        return self.get(agent_id, kind) is not None

    def for_kind(self, kind: str) -> tuple[InterfaceRegistration, ...]:
        self._validate_kind(kind)
        return tuple(
            registration
            for agent_id in self.manifests.ids()
            if (registration := self._entries.get((agent_id, kind))) is not None
        )

    def for_role(self, kind: str, role: str) -> tuple[InterfaceRegistration, ...]:
        if not isinstance(role, str) or not role.strip():
            raise ValueError('Interface role must be non-empty.')
        role = role.strip()
        return tuple(registration for registration in self.for_kind(kind) if role in registration.roles)

    def notifications_declared(self, agent_id: str) -> bool:
        self._validate_agent(agent_id)
        return self._notifications[agent_id]

    def describe_agent(self, agent_id: str) -> dict:
        manifest = self.manifests.get(agent_id)
        interfaces = {}
        for kind in INTERFACE_KIND_ORDER:
            registration = self._entries.get((agent_id, kind))
            interfaces[kind] = (
                {'available': False}
                if registration is None
                else {
                    'available': True,
                    'module': registration.module,
                    'roles': list(registration.roles),
                }
            )
        return {
            'id': manifest.id,
            'name': manifest.name,
            'version': manifest.version,
            'notifications': self._notifications[agent_id],
            'interfaces': interfaces,
        }

    def describe(self) -> list[dict]:
        return [self.describe_agent(agent_id) for agent_id in self.manifests.ids()]

    def __len__(self) -> int:
        return len(self._entries)
