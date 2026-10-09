"""Manifest-backed discovery registry kept separate from runtime execution authority."""
from __future__ import annotations

from agents.manifest import AgentManifest, INTERFACE_KINDS, validate_against_runtime
from agents.registry import AgentRegistry


class ManifestRegistry:
    """Validated discovery metadata for the runtime domains installed in Chief.

    The runtime ``AgentRegistry`` remains authoritative for execution. This registry
    only exposes manifest metadata after proving that each entry matches a concrete
    runtime domain/agent pair.
    """

    def __init__(self, runtime: AgentRegistry):
        if not isinstance(runtime, AgentRegistry):
            raise TypeError('ManifestRegistry requires an AgentRegistry runtime catalog.')
        self.runtime = runtime
        self._manifests: dict[str, AgentManifest] = {}
        self._runtime_agent_ids: dict[str, str] = {}

    def register(self, manifest: AgentManifest) -> AgentManifest:
        if not isinstance(manifest, AgentManifest):
            raise ValueError('Only validated AgentManifest values may be registered.')
        if manifest.id in self._manifests:
            raise ValueError('Duplicate agent manifest registration.')
        if manifest.id not in self.runtime.domains:
            raise ValueError('Agent manifest has no installed runtime domain.')
        runtime_agents = [agent for agent in self.runtime.agents.values() if agent.domain == manifest.id]
        if len(runtime_agents) != 1:
            raise ValueError('Discoverable runtime domains require exactly one runtime agent.')
        runtime_agent = runtime_agents[0]
        validate_against_runtime(manifest, self.runtime.domains[manifest.id], runtime_agent)
        self._manifests[manifest.id] = manifest
        self._runtime_agent_ids[manifest.id] = runtime_agent.id
        return manifest

    def require_complete(self) -> 'ManifestRegistry':
        missing = [domain_id for domain_id in self.runtime.domains if domain_id not in self._manifests]
        if missing:
            raise ValueError('Installed runtime domains require manifests: ' + ', '.join(missing))
        return self

    def get(self, manifest_id: str) -> AgentManifest:
        try:
            return self._manifests[manifest_id]
        except KeyError as exc:
            raise ValueError('Unknown agent manifest.') from exc

    def runtime_agent_id(self, manifest_id: str) -> str:
        self.get(manifest_id)
        return self._runtime_agent_ids[manifest_id]

    def ids(self) -> tuple[str, ...]:
        return tuple(self._manifests)

    def describe(self) -> list[dict]:
        return [
            {**manifest.as_dict(), 'runtime_agent_id': self._runtime_agent_ids[manifest_id]}
            for manifest_id, manifest in self._manifests.items()
        ]

    def for_interface(self, kind: str) -> tuple[AgentManifest, ...]:
        if kind not in INTERFACE_KINDS:
            raise ValueError(f'Unsupported agent interface: {kind!r}.')
        return tuple(
            manifest
            for manifest in self._manifests.values()
            if any(interface.kind == kind for interface in manifest.interfaces)
        )

    def __contains__(self, manifest_id: object) -> bool:
        return manifest_id in self._manifests

    def __len__(self) -> int:
        return len(self._manifests)
