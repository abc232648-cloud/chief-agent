from dataclasses import FrozenInstanceError

import pytest

from agents.manifest import InterfaceDeclaration
from agents.staff_roles import (
    STAFF_CAPABILITIES,
    STAFF_ROLE_ORDER,
    STAFF_ROLES,
    StaffRoleContract,
    describe_staff_roles,
    staff_role,
    validate_staff_roles,
)
from application.composition import default_interface_registry
from domains.farming import setup
from identity.contracts import MATRIX
from identity.service import IdentityService
from tests.test_farm_tasks import staff as farm_staff


def test_staff_role_contract_is_exact_deterministic_and_immutable():
    assert STAFF_ROLE_ORDER == ('Manager', 'Supervisor', 'Worker')
    assert list(STAFF_ROLES) == list(STAFF_ROLE_ORDER)
    assert describe_staff_roles() == [
        {
            'name': 'Manager',
            'scope': 'DOMAIN',
            'capabilities': [
                'staff.overview.read',
                'staff.sop.read',
                'staff.report.submit',
                'staff.escalation.submit',
                'staff.work.supervise',
                'staff.work.verify',
                'staff.work.correct',
                'staff.work.assign',
                'staff.work.manage',
            ],
            'authority': 'PRESENTATION_ONLY',
        },
        {
            'name': 'Supervisor',
            'scope': 'SUPERVISED',
            'capabilities': [
                'staff.overview.read',
                'staff.sop.read',
                'staff.report.submit',
                'staff.escalation.submit',
                'staff.work.supervise',
                'staff.work.verify',
                'staff.work.correct',
            ],
            'authority': 'PRESENTATION_ONLY',
        },
        {
            'name': 'Worker',
            'scope': 'ASSIGNED',
            'capabilities': [
                'staff.overview.read',
                'staff.sop.read',
                'staff.report.submit',
                'staff.escalation.submit',
                'staff.work.execute',
                'staff.work.submit',
            ],
            'authority': 'PRESENTATION_ONLY',
        },
    ]
    with pytest.raises(TypeError):
        STAFF_ROLES['Assistant Manager'] = staff_role('Worker')
    with pytest.raises(FrozenInstanceError):
        staff_role('Worker').scope = 'DOMAIN'


def test_role_constructor_cannot_forge_weaker_or_broader_canonical_role():
    with pytest.raises(ValueError, match='scope does not match'):
        StaffRoleContract('Supervisor', 'DOMAIN', staff_role('Supervisor').capabilities)
    with pytest.raises(ValueError, match='capabilities do not match'):
        StaffRoleContract('Worker', 'ASSIGNED', staff_role('Manager').capabilities)
    with pytest.raises(ValueError, match='Unknown Staff role'):
        StaffRoleContract('Assistant Manager', 'ASSIGNED', staff_role('Worker').capabilities)


def test_staff_capabilities_are_presentation_metadata_not_identity_permissions():
    identity_permissions = set().union(*MATRIX.values())
    assert STAFF_CAPABILITIES.isdisjoint(identity_permissions)
    assert all(capability.startswith('staff.') for capability in STAFF_CAPABILITIES)
    assert 'Supervisor' not in MATRIX
    assert 'identity.workers.manage' in MATRIX['Manager']
    assert 'identity.workers.manage' not in MATRIX['Worker']
    assert staff_role('Supervisor').allows('staff.work.verify')
    assert not staff_role('Supervisor').allows('staff.work.assign')
    assert not staff_role('Worker').allows('staff.work.verify')
    assert not staff_role('Worker').allows('staff.work.manage')
    assert set(staff_role('Supervisor').capabilities) < set(staff_role('Manager').capabilities)


def test_staff_interface_declarations_accept_only_canonical_staff_roles():
    declaration = InterfaceDeclaration('staff', 'farming', STAFF_ROLE_ORDER)
    assert declaration.roles == STAFF_ROLE_ORDER
    assert validate_staff_roles(('Supervisor', 'Worker')) == ('Supervisor', 'Worker')

    for roles in ((), ('Owner',), ('Administrator',), ('Assistant Manager',), ('manager',)):
        with pytest.raises(ValueError):
            InterfaceDeclaration('staff', 'farming', roles)

    # Owner/companion audiences remain separate presentation contracts.
    assert InterfaceDeclaration('owner', 'farming', ('Owner', 'Administrator')).roles == ('Owner', 'Administrator')


def test_default_interface_registry_exposes_typed_staff_contracts_without_authority():
    registry = default_interface_registry()
    roles = registry.staff_roles('farming')
    assert tuple(role.name for role in roles) == STAFF_ROLE_ORDER
    assert registry.staff_role('farming', 'Manager').scope == 'DOMAIN'
    assert registry.staff_role('farming', 'Supervisor').scope == 'SUPERVISED'
    assert registry.staff_role('farming', 'Worker').scope == 'ASSIGNED'

    with pytest.raises(ValueError, match='Unknown Staff role'):
        registry.staff_role('farming', 'Owner')
    with pytest.raises(ValueError, match='does not expose a.*staff.*interface'):
        registry.staff_roles('jobs')


def test_farm_supervisor_remains_worker_identity_with_domain_scoped_supervision(dashboard):
    d = dashboard
    _, _, _, _, _, _, _, _, _, _, supervisor = farm_staff(d)
    service = IdentityService(d.store)

    assert supervisor.role == 'Worker'
    assert service.authorize(supervisor, 'work.request', 'farming', sensitive=False) == 'ROLE'
    with pytest.raises(PermissionError):
        service.authorize(supervisor, 'identity.workers.manage', 'farming', sensitive=False)

    with d.store._connect() as con:
        assert setup.role(con, supervisor) == 'SUPERVISOR'
        assert setup.authorize(d.store, con, supervisor, 'manage').id == supervisor.id
        with pytest.raises(PermissionError, match='Farm management authority is required'):
            setup.authorize(d.store, con, supervisor, 'setup')
