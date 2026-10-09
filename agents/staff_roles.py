"""Stable Staff PWA role metadata; never an authorization source.

These role capabilities describe workflow surfaces that a Staff interface may
present. They are intentionally separate from identity permissions, agent
capabilities and domain authorization. A caller must still pass Chief's normal
identity, domain-scope, assignment and state checks before any operation occurs.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

STAFF_ROLE_ORDER = ('Manager', 'Supervisor', 'Worker')
STAFF_SCOPES = frozenset({'DOMAIN', 'SUPERVISED', 'ASSIGNED'})
STAFF_CAPABILITIES = frozenset({
    'staff.overview.read',
    'staff.sop.read',
    'staff.report.submit',
    'staff.escalation.submit',
    'staff.work.assign',
    'staff.work.manage',
    'staff.work.supervise',
    'staff.work.verify',
    'staff.work.correct',
    'staff.work.execute',
    'staff.work.submit',
})

_COMMON = (
    'staff.overview.read',
    'staff.sop.read',
    'staff.report.submit',
    'staff.escalation.submit',
)
_SUPERVISION = (
    'staff.work.supervise',
    'staff.work.verify',
    'staff.work.correct',
)
_ROLE_SPECS = MappingProxyType({
    'Manager': ('DOMAIN', _COMMON + _SUPERVISION + ('staff.work.assign', 'staff.work.manage')),
    'Supervisor': ('SUPERVISED', _COMMON + _SUPERVISION),
    'Worker': ('ASSIGNED', _COMMON + ('staff.work.execute', 'staff.work.submit')),
})


@dataclass(frozen=True)
class StaffRoleContract:
    """Presentation/workflow contract for one canonical Staff role."""

    name: str
    scope: str
    capabilities: tuple[str, ...]

    def __post_init__(self):
        expected = _ROLE_SPECS.get(self.name)
        if expected is None:
            raise ValueError('Unknown Staff role.')
        if self.scope not in STAFF_SCOPES or self.scope != expected[0]:
            raise ValueError('Staff role scope does not match the canonical contract.')
        if not isinstance(self.capabilities, tuple) or self.capabilities != expected[1]:
            raise ValueError('Staff role capabilities do not match the canonical contract.')
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError('Staff role capabilities must be unique.')
        if any(capability not in STAFF_CAPABILITIES for capability in self.capabilities):
            raise ValueError('Unknown Staff workflow capability.')

    def allows(self, capability: str) -> bool:
        return capability in self.capabilities

    def as_dict(self) -> dict:
        return {
            'name': self.name,
            'scope': self.scope,
            'capabilities': list(self.capabilities),
            'authority': 'PRESENTATION_ONLY',
        }


STAFF_ROLES = MappingProxyType({
    name: StaffRoleContract(name, scope, capabilities)
    for name, (scope, capabilities) in _ROLE_SPECS.items()
})


def staff_role(name: str) -> StaffRoleContract:
    """Return one canonical Staff role or fail closed."""

    if not isinstance(name, str) or name not in STAFF_ROLES:
        raise ValueError('Unknown Staff role.')
    return STAFF_ROLES[name]


def validate_staff_roles(names) -> tuple[str, ...]:
    """Validate one non-empty ordered Staff-interface audience declaration."""

    names = tuple(names)
    if not names:
        raise ValueError('Staff interfaces must declare at least one supported Staff role.')
    if len(names) != len(set(names)):
        raise ValueError('Staff interface roles must be unique.')
    for name in names:
        staff_role(name)
    return names


def describe_staff_roles(names=STAFF_ROLE_ORDER) -> list[dict]:
    """Detached role metadata suitable for interface discovery responses later."""

    return [staff_role(name).as_dict() for name in validate_staff_roles(names)]
