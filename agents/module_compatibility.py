"""Read-only compatibility evaluation for validated Chief agent manifests.

Phase A8 turns the declarative ``AgentManifest.requires`` contract into a bounded,
fail-closed observation. Runtime execution authority remains in AgentRegistry and
ComponentControls; this module never installs, enables, disables or updates anything.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from agents.discovery import ManifestRegistry
from agents.manifest import AgentManifest, INTERFACE_KINDS


_SEMVER_RE = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')
_REQUIREMENT_RE = re.compile(r'^(>=|<=|==|=|>|<)?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')
_API_RE = re.compile(r'^(0|[1-9]\d*)$')


@dataclass(frozen=True)
class ChiefCompatibilityProfile:
    """Versioned contracts exposed by this Chief runtime."""

    chief_version: str
    owner_api: str
    staff_api: str
    companion_api: str

    def __post_init__(self):
        if not isinstance(self.chief_version, str) or not _SEMVER_RE.fullmatch(self.chief_version):
            raise ValueError('Chief compatibility version must use major.minor.patch.')
        for name in ('owner_api', 'staff_api', 'companion_api'):
            value = getattr(self, name)
            if not isinstance(value, str) or not _API_RE.fullmatch(value):
                raise ValueError(f'{name.replace("_", " ").title()} compatibility level must be a non-negative integer string.')

    def interface_api(self, kind: str) -> str:
        if kind not in INTERFACE_KINDS:
            raise ValueError(f'Unsupported agent interface: {kind!r}.')
        return getattr(self, f'{kind}_api')


CURRENT_CHIEF_COMPATIBILITY = ChiefCompatibilityProfile(
    chief_version='1.0.0',
    owner_api='1',
    staff_api='1',
    companion_api='1',
)


@dataclass(frozen=True)
class CompatibilityIssue:
    code: str
    message: str

    def as_dict(self) -> dict:
        return {'code': self.code, 'message': self.message}


@dataclass(frozen=True)
class ModuleCompatibilityResult:
    module_id: str
    compatible: bool
    chief_version: str
    issues: tuple[CompatibilityIssue, ...] = ()

    @property
    def reason(self) -> str:
        if self.compatible:
            return ''
        return '; '.join(issue.message for issue in self.issues)[:500]

    def as_dict(self) -> dict:
        return {
            'module_id': self.module_id,
            'compatible': self.compatible,
            'chief_version': self.chief_version,
            'issues': [issue.as_dict() for issue in self.issues],
            'reason': self.reason,
            'authority': 'CHIEF_DERIVED',
        }


def _semver(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not _SEMVER_RE.fullmatch(value):
        raise ValueError('Compatibility versions must use major.minor.patch.')
    return tuple(int(part) for part in value.split('.'))


def _chief_requirement_accepts(requirement: str, current_version: str) -> bool:
    """Evaluate an AND-only comparator range such as ``>=1.0.0 <2.0.0``.

    Unsupported range syntax fails closed instead of being guessed or widened.
    """

    if not isinstance(requirement, str) or not requirement.strip():
        raise ValueError('Chief compatibility requirement must not be empty.')
    current = _semver(current_version)
    clauses = requirement.split()
    if not clauses:
        raise ValueError('Chief compatibility requirement must contain at least one clause.')
    for clause in clauses:
        match = _REQUIREMENT_RE.fullmatch(clause)
        if match is None:
            raise ValueError('Unsupported Chief compatibility requirement syntax.')
        operator = match.group(1) or '=='
        target = tuple(int(match.group(i)) for i in (2, 3, 4))
        accepted = {
            '>=': current >= target,
            '>': current > target,
            '<=': current <= target,
            '<': current < target,
            '==': current == target,
            '=': current == target,
        }[operator]
        if not accepted:
            return False
    return True


def evaluate_manifest(
    manifest: AgentManifest,
    profile: ChiefCompatibilityProfile = CURRENT_CHIEF_COMPATIBILITY,
) -> ModuleCompatibilityResult:
    """Return a deterministic, side-effect-free compatibility result for one manifest."""

    if not isinstance(manifest, AgentManifest):
        raise TypeError('Compatibility evaluation requires a validated AgentManifest.')
    if not isinstance(profile, ChiefCompatibilityProfile):
        raise TypeError('Compatibility evaluation requires a ChiefCompatibilityProfile.')

    issues: list[CompatibilityIssue] = []
    try:
        chief_ok = _chief_requirement_accepts(manifest.requires.chief, profile.chief_version)
    except ValueError:
        issues.append(CompatibilityIssue(
            'CHIEF_REQUIREMENT_INVALID',
            'Manifest declares an unsupported Chief compatibility requirement.',
        ))
    else:
        if not chief_ok:
            issues.append(CompatibilityIssue(
                'CHIEF_VERSION_MISMATCH',
                f'Chief {profile.chief_version} does not satisfy manifest requirement {manifest.requires.chief}.',
            ))

    declared_kinds = {item.kind for item in manifest.interfaces}
    for kind in sorted(INTERFACE_KINDS):
        declared_requirement = getattr(manifest.requires, f'{kind}_api')
        if kind in declared_kinds:
            if declared_requirement is None:
                issues.append(CompatibilityIssue(
                    'INTERFACE_REQUIREMENT_MISSING',
                    f'Manifest exposes {kind} interface without declaring {kind}_api compatibility.',
                ))
                continue
            if not _API_RE.fullmatch(declared_requirement):
                issues.append(CompatibilityIssue(
                    'INTERFACE_REQUIREMENT_INVALID',
                    f'Manifest declares an invalid {kind}_api compatibility level.',
                ))
                continue
            current_api = profile.interface_api(kind)
            if declared_requirement != current_api:
                issues.append(CompatibilityIssue(
                    'INTERFACE_API_MISMATCH',
                    f'Manifest requires {kind}_api {declared_requirement}, but Chief exposes {current_api}.',
                ))
        elif declared_requirement is not None:
            issues.append(CompatibilityIssue(
                'INTERFACE_REQUIREMENT_ORPHANED',
                f'Manifest declares {kind}_api compatibility without exposing a {kind} interface.',
            ))

    return ModuleCompatibilityResult(
        module_id=manifest.id,
        compatible=not issues,
        chief_version=profile.chief_version,
        issues=tuple(issues),
    )


class ModuleCompatibilityEvaluator:
    """Evaluate registered module manifests without mutating runtime authority."""

    def __init__(
        self,
        manifests: ManifestRegistry,
        profile: ChiefCompatibilityProfile = CURRENT_CHIEF_COMPATIBILITY,
    ):
        if not isinstance(manifests, ManifestRegistry):
            raise TypeError('ModuleCompatibilityEvaluator requires the authoritative ManifestRegistry.')
        if not isinstance(profile, ChiefCompatibilityProfile):
            raise TypeError('ModuleCompatibilityEvaluator requires a ChiefCompatibilityProfile.')
        self.manifests = manifests
        self.profile = profile

    def evaluate(self, module_id: str) -> ModuleCompatibilityResult:
        return evaluate_manifest(self.manifests.get(module_id), self.profile)

    def describe(self) -> list[dict]:
        return [self.evaluate(module_id).as_dict() for module_id in self.manifests.ids()]
