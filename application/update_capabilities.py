"""Human-only source selection is separate from planning and agent grants."""
from dataclasses import replace
from capabilities.contracts import Consumer, Dependency, Node
from .checkpoint_g_capabilities import capabilities as planning_capabilities


def capabilities():
    tests = ('tests/test_update_coordinator.py', 'tests/test_update_approval.py',
             'tests/test_update_recovery.py', 'tests/test_managed_updates.py')
    return (replace(planning_capabilities()[0],
        id='chief.update_source_selection',
        description='Explicit human-approved source-only selection under verified recovery locks; never automatic agent installation.',
        dependencies=(Dependency(Node('capability', 'chief.update_planning')),),
        consumers=(Consumer('chief.update-center', True, tests),),
        # Catalog permissions describe component grants, not human RBAC roles.
        permissions=(), tests=tests,
        frameworks=('Human installation.manage with reauthentication; capability declaration grants no agent authority.',),
        inputs='Verified candidate, server-owned acceptance, single-use reauthenticated review and private recovery configuration.',
        outputs='Source selection requiring manual restart; backup and quarantined restore references.',
        side_effects=('Private source pointer and update audit records only; no database migration or automatic service start.',),
        failure_behavior='Any failed authority, trust, evidence, control or recovery gate blocks selection.',
        overrides=('No AgentRegistry grant or runtime executor is added. Existing human installation authority remains required.',),
        audit=('Existing human_security_events and private update control_state references.',)),)
