"""Explicit descriptors of existing shared mechanics, not new implementations."""
from capabilities.contracts import Capability, Component, ComponentKind, Consumer, Dependency, Maturity, Mode, Node


def component_dependencies(*names):
    return tuple(Dependency(Node('component', name)) for name in names)


def capability_dependencies(*names):
    return tuple(Dependency(Node('capability', name)) for name in names)


def shared_components():
    # Versions identify these adapter contracts, not application/vendor releases.
    return (
        Component('chief', ComponentKind.CORE),
        Component('chief.database', ComponentKind.SERVICE, tests=('tests/test_database.py', 'tests/test_database_connection_lifetime.py')),
        Component('chief.policy', ComponentKind.SERVICE, tests=('tests/test_policy.py', 'tests/test_worker_policy_gate.py', 'tests/test_policy_foundation.py', 'tests/test_policy_equivalence.py')),
        Component('chief.component_controls', ComponentKind.SERVICE,
                  dependencies=component_dependencies('chief.database'),
                  tests=('tests/test_component_controls.py','tests/test_component_state_migration.py','tests/test_checkpoint_c_compatibility.py')),
        Component('chief.system_health', ComponentKind.SERVICE,
                  dependencies=component_dependencies('chief.runtime','chief.component_controls'),
                  tests=('tests/test_component_health.py',)),
        Component('chief.gateway', ComponentKind.SERVICE, tests=('tests/test_gateway.py',)),
        Component('chief.browser', ComponentKind.SERVICE, tests=('tests/test_access_browser.py', 'tests/test_playwright_reader.py', 'tests/test_site_access.py')),
        Component('chief.notifications', ComponentKind.SERVICE, tests=('tests/test_notifications.py', 'tests/test_chief_controls.py')),
        Component('chief.runtime', ComponentKind.SERVICE, tests=('tests/test_domains.py', 'tests/test_worker_composition.py')),
        Component('chief.dashboard', ComponentKind.SERVICE,
                  tests=('tests/test_dashboard_runtime.py', 'tests/test_dashboard_browser.py', 'tests/test_chief_browser.py')),
        Component('chief.scheduler', ComponentKind.SERVICE,
                  tests=('tests/test_v25_scheduler.py', 'tests/test_v25_dashboard_scheduler.py', 'tests/test_v19_monitoring.py')),
    )


def shared_capabilities():
    controls = ('tests/test_chief_controls.py', 'tests/test_worker_composition.py', 'tests/test_component_controls.py', 'tests/test_component_state_migration.py', 'tests/test_component_health.py', 'tests/test_checkpoint_c_compatibility.py')
    domain = ('tests/test_domains.py', 'tests/test_domains_browser.py')
    return (
        Capability(
            id='chief.agent_controls', owner='chief', version='1.0.0',
            description='Existing persisted enabled/running/autostart controls and worker activity.',
            maturity=Maturity.LIMITED, mode=Mode.LEGACY_CONTROLLED,
            dependencies=component_dependencies('chief.database'),
            consumers=(Consumer('jobs-worker', True, controls), Consumer('farming-recorder', True, controls + domain),
                       Consumer('chief.dashboard', True, ('tests/test_chief_browser.py', 'tests/test_dashboard_runtime.py')),
                       Consumer('chief.scheduler', True, ('tests/test_v25_scheduler.py', 'tests/test_v19_monitoring.py'))),
            permissions=(), frameworks=('Existing capability enforcement; no new policy pack.',),
            data_access=('agent_controls, agent_runs, control_state; no private domain data sharing',),
            models=(), tests=controls, health_dependencies=('chief.database', 'chief.runtime'),
            inputs='Registered domain identity and existing boolean controls.', outputs='Existing allowed/overview/claim results.',
            side_effects=('Persist control changes, activity and heartbeat; audit control changes.',),
            failure_behavior='Paused/disabled agents cannot begin new guarded work; existing failure semantics remain.',
            overrides=('Legacy enabled/running/autostart settings remain authoritative.',),
            audit=('Existing agent control and run records.',)),
        Capability(
            id='chief.domain_storage', owner='chief', version='1.0.0',
            description='Existing context-scoped records, reminders and domain request storage.',
            maturity=Maturity.LIMITED, mode=Mode.LEGACY_CONTROLLED,
            dependencies=component_dependencies('chief.database'),
            consumers=(Consumer('farming-recorder', True, domain),), permissions=(),
            frameworks=('WorkerContext supplies namespace and enforces caller capability.',),
            data_access=('domain_records, domain_reminders and domain_requests under trusted domain scope',),
            models=(), tests=domain, health_dependencies=('chief.database',),
            inputs='Trusted domain context and domain-owned payload.', outputs='Existing records/reminders/IDs.',
            side_effects=('SQLite writes in the current transaction where supplied.',),
            failure_behavior='Permission errors and transaction rollback remain unchanged.',
            overrides=(), audit=('Domain runtime retains existing action audit.',)),
        Capability(
            id='chief.domain_dispatch', owner='chief', version='1.0.0',
            description='Existing shared dispatch, including compatibility routing of legacy commands to Job.',
            maturity=Maturity.LIMITED, mode=Mode.LEGACY_CONTROLLED,
            dependencies=capability_dependencies('chief.agent_controls', 'chief.domain_storage') + component_dependencies('chief.policy', 'chief.runtime'),
            consumers=(Consumer('jobs-worker', True, ('tests/test_command_processor.py', 'tests/test_worker_composition.py', 'tests/test_v24_recovery.py')),
                       Consumer('farming-recorder', True, domain),
                       Consumer('chief.dashboard', True, ('tests/test_dashboard_runtime.py', 'tests/test_domains_browser.py'))),
            permissions=(), frameworks=('Existing forbidden/high-impact local-route rejection and WorkerContext checks.',),
            data_access=('Command/action/run state; private records only through existing domain APIs.',),
            models=(), tests=('tests/test_domains.py', 'tests/test_application_composition.py'),
            health_dependencies=('chief.database', 'chief.runtime'),
            inputs='Registered domain action or legacy Job command/approved action.', outputs='Existing command/action result.',
            side_effects=('Claim/update work and existing audit; delegate only through current guarded processors.',),
            failure_behavior='Existing pause, failure and interrupted-action REVIEW semantics.',
            overrides=('No registry declaration overrides live permission or approval decisions.',),
            audit=('Existing command, agent run and domain audit history.',)),
        Capability(
            id='chief.reminder_delivery', owner='chief', version='1.0.0',
            description='Existing atomic due-reminder delivery to the dashboard inbox.',
            maturity=Maturity.LIMITED, mode=Mode.LEGACY_CONTROLLED,
            dependencies=capability_dependencies('chief.agent_controls', 'chief.domain_storage') + component_dependencies('chief.notifications'),
            consumers=(Consumer('farming-recorder', True, domain + ('tests/test_chief_controls.py',)),
                       Consumer('chief.scheduler', True, ('tests/test_v25_scheduler.py',))),
            permissions=(), frameworks=('Existing domain availability checks.',),
            data_access=('Due reminders for enabled/running registered domains; notification inbox.',),
            models=(), tests=('tests/test_domains.py', 'tests/test_chief_controls.py', 'tests/test_v25_scheduler.py'),
            health_dependencies=('chief.database', 'chief.notifications'),
            inputs='Store and injected domain controls.', outputs='Delivered reminder count.',
            side_effects=('Insert inbox notification and mark delivered in one transaction.',),
            failure_behavior='Paused domains defer delivery; failure must not partially commit.',
            overrides=(), audit=('Existing inbox and reminder status.',)),
    )
