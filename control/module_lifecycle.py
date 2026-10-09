"""Read-only module lifecycle projection over existing Chief authorities.

Lifecycle is deliberately derived. ComponentControls remains authoritative for desired
runtime mode, SystemHealth remains authoritative for observed health, ManifestRegistry
remains authoritative for installed runtime discovery, and Update Center / compatibility
phases may provide bounded trusted evidence. This module never mutates those systems.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Mapping
import re

from agents.discovery import ManifestRegistry
from agents.module_compatibility import ModuleCompatibilityEvaluator
from capabilities.contracts import Mode, Node
from control.components import ComponentControls
from control.health import SystemHealth
from control.health_contracts import HealthResult, HealthStatus


class ModuleState(str, Enum):
    NOT_INSTALLED = 'NOT_INSTALLED'
    INSTALLED = 'INSTALLED'
    DISABLED = 'DISABLED'
    ENABLED = 'ENABLED'
    DEGRADED = 'DEGRADED'
    UPDATE_AVAILABLE = 'UPDATE_AVAILABLE'
    INCOMPATIBLE = 'INCOMPATIBLE'


_ID_RE = re.compile(r'^[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*$')
_VERSION_RE = re.compile(r'^[0-9A-Za-z][0-9A-Za-z.+_-]{0,79}$')


def _module_id(value: str) -> str:
    if not isinstance(value, str) or len(value) > 80 or not _ID_RE.fullmatch(value):
        raise ValueError('Module id must be a bounded lowercase stable identifier.')
    return value


def _version(value: str, label: str) -> str:
    if not isinstance(value, str) or not _VERSION_RE.fullmatch(value):
        raise ValueError(f'{label} must be a bounded version identifier.')
    return value


@dataclass(frozen=True)
class ModuleLifecycleEvidence:
    """Trusted observations owned by installer/update phases and legacy A7 callers.

    ``activated=False`` exists so an eventual installer inventory can represent
    INSTALLED-before-activation. ``validated_update_version`` must only be supplied
    after the existing Update Center (or a future module updater using the same gates)
    validates a candidate. When the A8 compatibility evaluator is attached to the
    lifecycle, compatibility fields are evaluator-owned and caller values are rejected.
    """

    activated: bool = True
    compatible: bool | None = None
    compatibility_reason: str = ''
    validated_update_version: str | None = None

    def __post_init__(self):
        if type(self.activated) is not bool:
            raise ValueError('Module activation evidence must be boolean.')
        if self.compatible is not None and type(self.compatible) is not bool:
            raise ValueError('Module compatibility evidence must be boolean or unknown.')
        if not isinstance(self.compatibility_reason, str) or len(self.compatibility_reason) > 500:
            raise ValueError('Module compatibility reason must be bounded text.')
        if self.compatible is False and not self.compatibility_reason.strip():
            raise ValueError('Incompatible module evidence requires an explicit reason.')
        if self.validated_update_version is not None:
            object.__setattr__(
                self,
                'validated_update_version',
                _version(self.validated_update_version, 'Validated update version'),
            )


@dataclass(frozen=True)
class ModuleLifecycleSnapshot:
    module_id: str
    runtime_agent_id: str | None
    version: str | None
    state: ModuleState
    desired_mode: str | None
    desired_revision: int | None
    health: str | None
    compatible: bool | None
    update_version: str | None
    reason: str
    authority: str = 'CHIEF_DERIVED'

    def as_dict(self) -> dict:
        return {
            'module_id': self.module_id,
            'runtime_agent_id': self.runtime_agent_id,
            'version': self.version,
            'state': self.state.value,
            'desired_mode': self.desired_mode,
            'desired_revision': self.desired_revision,
            'health': self.health,
            'compatible': self.compatible,
            'update_version': self.update_version,
            'reason': self.reason,
            'authority': self.authority,
        }


class ModuleLifecycle:
    """Aggregate existing Chief state into one presentation-safe module lifecycle."""

    def __init__(
        self,
        manifests: ManifestRegistry,
        controls: ComponentControls,
        health: SystemHealth,
        compatibility: ModuleCompatibilityEvaluator | None = None,
    ):
        if not isinstance(manifests, ManifestRegistry):
            raise TypeError('ModuleLifecycle requires the authoritative ManifestRegistry.')
        if not isinstance(controls, ComponentControls):
            raise TypeError('ModuleLifecycle requires ComponentControls.')
        if not isinstance(health, SystemHealth):
            raise TypeError('ModuleLifecycle requires SystemHealth.')
        if compatibility is not None and not isinstance(compatibility, ModuleCompatibilityEvaluator):
            raise TypeError('ModuleLifecycle compatibility must use ModuleCompatibilityEvaluator.')
        if compatibility is not None and compatibility.manifests is not manifests:
            raise ValueError('ModuleLifecycle compatibility must evaluate the same ManifestRegistry.')
        self.manifests = manifests
        self.controls = controls
        self.health = health
        self.compatibility = compatibility

    def _health_index(self) -> dict[Node, HealthResult]:
        result: dict[Node, HealthResult] = {}
        for item in self.health.snapshot():
            if not isinstance(item, HealthResult):
                raise ValueError('System health returned an invalid lifecycle observation.')
            if item.node in result:
                raise ValueError('System health returned duplicate lifecycle observations.')
            result[item.node] = item
        return result

    def _compatibility_evidence(
        self,
        module_id: str,
        evidence: ModuleLifecycleEvidence,
    ) -> ModuleLifecycleEvidence:
        if self.compatibility is None:
            return evidence
        if evidence.compatible is not None or evidence.compatibility_reason.strip():
            raise ValueError('Compatibility evidence is owned by the A8 evaluator for this lifecycle.')
        result = self.compatibility.evaluate(module_id)
        return replace(
            evidence,
            compatible=result.compatible,
            compatibility_reason=result.reason,
        )

    def _known_snapshot(
        self,
        module_id: str,
        health_index: Mapping[Node, HealthResult],
        evidence: ModuleLifecycleEvidence,
    ) -> ModuleLifecycleSnapshot:
        manifest = self.manifests.get(module_id)
        runtime_agent_id = self.manifests.runtime_agent_id(module_id)
        node = Node('component', runtime_agent_id)
        desired = self.controls.desired(node)
        observed = health_index.get(node)
        health_status = observed.status if observed is not None else HealthStatus.UNKNOWN
        evidence = self._compatibility_evidence(module_id, evidence)

        if evidence.validated_update_version == manifest.version:
            raise ValueError('Validated update version must differ from the installed module version.')

        if evidence.compatible is False:
            state = ModuleState.INCOMPATIBLE
            reason = evidence.compatibility_reason.strip()
        elif not evidence.activated:
            state = ModuleState.INSTALLED
            reason = 'Installed manifest is present but module activation is not complete.'
        elif desired.mode != Mode.ENABLED:
            state = ModuleState.DISABLED
            reason = f'Chief desired mode is {desired.mode.value}.'
        elif health_status in {HealthStatus.DEGRADED, HealthStatus.UNAVAILABLE}:
            state = ModuleState.DEGRADED
            reason = observed.reason if observed is not None and observed.reason else f'Observed health is {health_status.value}.'
        elif evidence.validated_update_version is not None:
            state = ModuleState.UPDATE_AVAILABLE
            reason = f'Validated update {evidence.validated_update_version} is available.'
        else:
            state = ModuleState.ENABLED
            reason = f'Runtime module is enabled; observed health is {health_status.value}.'

        return ModuleLifecycleSnapshot(
            module_id=module_id,
            runtime_agent_id=runtime_agent_id,
            version=manifest.version,
            state=state,
            desired_mode=desired.mode.value,
            desired_revision=desired.revision,
            health=health_status.value,
            compatible=evidence.compatible,
            update_version=evidence.validated_update_version,
            reason=reason,
        )

    def snapshot(self, module_id: str, *, evidence: ModuleLifecycleEvidence | None = None, observe_health: bool = True) -> ModuleLifecycleSnapshot:
        module_id = _module_id(module_id)
        if module_id not in self.manifests:
            if evidence is not None:
                raise ValueError('Lifecycle evidence cannot target a module that is not installed.')
            return ModuleLifecycleSnapshot(
                module_id=module_id,
                runtime_agent_id=None,
                version=None,
                state=ModuleState.NOT_INSTALLED,
                desired_mode=None,
                desired_revision=None,
                health=None,
                compatible=None,
                update_version=None,
                reason='No validated installed manifest exists for this module id.',
            )
        if evidence is None:
            evidence = ModuleLifecycleEvidence()
        if not isinstance(evidence, ModuleLifecycleEvidence):
            raise ValueError('Lifecycle evidence must use the trusted ModuleLifecycleEvidence contract.')
        # Exposure checks need current lifecycle/compatibility authority, not a
        # full-system observational health scan on every HTTP request.
        if type(observe_health) is not bool:
            raise ValueError('Health observation selection must be boolean.')
        return self._known_snapshot(module_id, self._health_index() if observe_health else {}, evidence)

    def describe(self, *, evidence: Mapping[str, ModuleLifecycleEvidence] | None = None) -> list[dict]:
        evidence = {} if evidence is None else evidence
        if not isinstance(evidence, Mapping):
            raise ValueError('Lifecycle evidence must be a module-id mapping.')
        unknown = set(evidence) - set(self.manifests.ids())
        if unknown:
            raise ValueError('Lifecycle evidence references modules that are not installed.')
        health_index = self._health_index()
        result = []
        for module_id in self.manifests.ids():
            item = evidence.get(module_id, ModuleLifecycleEvidence())
            if not isinstance(item, ModuleLifecycleEvidence):
                raise ValueError('Lifecycle evidence must use the trusted ModuleLifecycleEvidence contract.')
            result.append(self._known_snapshot(module_id, health_index, item).as_dict())
        return result
