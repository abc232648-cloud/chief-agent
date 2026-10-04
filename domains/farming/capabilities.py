"""Describe Farm records and reminders; poultry journal is a human-only isolated pilot."""
from capabilities.contracts import Capability, Consumer, Dependency, Maturity, Mode, Node


TESTS = ('tests/test_domains.py', 'tests/test_domains_browser.py', 'tests/test_chief_controls.py', 'tests/test_farm_journal.py')


def definitions():
    shared = dict(owner='farming', version='1.0.0', maturity=Maturity.LIMITED, mode=Mode.LEGACY_CONTROLLED,
                  frameworks=('Existing WorkerContext namespace/capability enforcement.',),
                  models=(), tests=TESTS, health_dependencies=('chief.database',), overrides=(),
                  consumers=(Consumer('farming-recorder', True, TESTS),),
                  audit=('Existing domain action audit and command/run records.',))
    return (
        Capability(id='farming.records.read', description='Read domain-scoped records/reminders; authorized humans can read the isolated poultry journal.',
                   dependencies=(Dependency(Node('capability', 'chief.domain_storage')),),
                   permissions=('farming.records.read',), data_access=('Farm domain records/reminders; isolated human poultry journal.',),
                   inputs='Trusted farming context.', outputs='Existing records and reminders.', side_effects=(),
                   failure_behavior='Other domains remain inaccessible through scoped storage.', **shared),
        Capability(id='farming.records.write', description='Record confirmed soil results; gate isolated human poultry journal writes without granting an agent action.',
                   dependencies=(Dependency(Node('capability', 'chief.domain_dispatch')),
                                 Dependency(Node('capability', 'chief.domain_storage'))),
                   permissions=('farming.records.write',), data_access=('Farm soil records and the isolated human poultry journal.',),
                   inputs='Existing plot/sample_date/ph/source/confirmed payload.', outputs='Existing soil record ID and status.',
                   side_effects=('Insert user-recorded soil evidence in domain-scoped storage.',),
                   failure_behavior='Invalid/unconfirmed measurements fail without a committed record.', **shared),
        Capability(id='farming.reminders.write', description='Schedule/cancel the existing Farm dashboard reminders.',
                   dependencies=(Dependency(Node('capability', 'chief.domain_dispatch')),
                                 Dependency(Node('capability', 'chief.domain_storage')),
                                 Dependency(Node('capability', 'chief.reminder_delivery'))),
                   permissions=('farming.reminders.write',), data_access=('Farm placeholder reminders only.',),
                   inputs='Existing schedule title/due_at or cancellation reminder_id.', outputs='Existing reminder ID/status.',
                   side_effects=('Create/cancel reminder; later delivery uses current inbox path.',),
                   failure_behavior='Reject invalid times/IDs and cross-domain cancellation; delivery remains deduplicated.', **shared),
    )
