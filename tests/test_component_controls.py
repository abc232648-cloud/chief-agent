from dataclasses import replace
import pytest
from checkpoint_c_fixture import migrated, unmigrated
from capabilities.contracts import Mode,Node
from control.agents import AgentControls
from control.component_contracts import StaleTransition
from control.components import ComponentControls
from application.control_services import compose_control_services
from security.permissions import worker_context


def change(fixture,node,mode):
    controls=fixture.services.controls
    return controls.transition(controls.preview(node,mode),actor='fixture',reason='Synthetic test',confirmed=True)


def test_agent_mode_persists_without_overwriting_domain_flags(migrated):
    node=Node('component','jobs-worker')
    change(migrated,node,Mode.MAINTENANCE)
    desired=compose_control_services(migrated.store).controls.desired(node)
    assert desired.mode==Mode.MAINTENANCE and desired.revision==1
    with migrated.store._connect() as con:
        row=con.execute("SELECT enabled,autostart,running FROM agent_controls WHERE domain='jobs'").fetchone()
        assert tuple(row)==(1,0,0)
    change(migrated,node,Mode.ENABLED)
    assert not migrated.controls.allowed('jobs')
    assert dict(migrated.services.controls.desired(Node('component','jobs')).legacy_controls)=={'enabled':1,'autostart':0,'running':0}


def test_disabled_capability_defers_claim_and_blocks_guarded_operations(migrated):
    migrated.controls.change('jobs',{'running':True})
    node=Node('capability','jobs.execute')
    preview=migrated.services.controls.preview(node,Mode.DISABLED)
    assert Node('component','jobs-worker') in preview.affected and preview.requires_confirmation
    with pytest.raises(ValueError,match='Confirm'):
        migrated.services.controls.transition(preview,actor='test',reason='test')
    change(migrated,node,Mode.DISABLED)
    assert migrated.controls.claim_command() is None
    _,agent=migrated.registry.resolve('jobs')
    with worker_context(agent,migrated.controls.allowed) as context:
        with pytest.raises(PermissionError):context.require('jobs.execute')
    change(migrated,node,Mode.ENABLED)
    assert migrated.controls.claim_command() is not None


@pytest.mark.parametrize('node,mode',[(Node('component','chief.database'),Mode.DISABLED),
                                    (Node('capability','chief.domain_storage'),Mode.MAINTENANCE),
                                    (Node('component','jobs'),Mode.DISABLED),
                                    (Node('component','jobs-worker'),Mode.SHADOW)])
def test_unsafe_or_unsupported_modes_rejected(migrated,node,mode):
    with pytest.raises(ValueError):migrated.services.controls.preview(node,mode)


def test_preview_revalidated_against_modes_and_legacy_controls(migrated):
    controls=migrated.services.controls
    node=Node('component','jobs-worker')
    first=controls.preview(node,Mode.DISABLED)
    migrated.controls.change('jobs',{'running':True})
    with pytest.raises(StaleTransition):controls.transition(first,actor='test',reason='test')
    first=controls.preview(node,Mode.DISABLED)
    change(migrated,Node('component','farming-recorder'),Mode.MAINTENANCE)
    with pytest.raises(StaleTransition):controls.transition(first,actor='test',reason='test')


def test_catalog_change_or_tampered_impact_invalidates_preview(migrated):
    controls=migrated.services.controls
    first=controls.preview(Node('capability','jobs.execute'),Mode.DISABLED)
    with pytest.raises(StaleTransition):
        controls.transition(replace(first,affected=(),requires_confirmation=False),actor='test',reason='test')
    second=ComponentControls(migrated.store,controls.catalog,supported=controls.supported,guarded_consumers=())
    with pytest.raises(ValueError,match='safe control adapter'):
        second.preview(first.node,first.mode)


def test_failed_audit_rolls_back_mode(migrated):
    controls=migrated.services.controls
    node=Node('component','jobs-worker')
    with migrated.store._connect() as con:
        con.execute("CREATE TRIGGER reject_component_audit BEFORE INSERT ON audit_log WHEN NEW.category='component_control' BEGIN SELECT RAISE(ABORT,'synthetic audit failure'); END")
    with pytest.raises(Exception):change(migrated,node,Mode.DISABLED)
    assert controls.desired(node).revision==0


def test_no_lazy_migration_from_transition(unmigrated):
    controls=compose_control_services(unmigrated.store).controls
    preview=controls.preview(Node('component','jobs-worker'),Mode.DISABLED)
    with pytest.raises(ValueError,match='migration'):
        controls.transition(preview,actor='test',reason='test')


def test_inflight_operation_finishes_next_boundary_stops(migrated):
    migrated.controls.change('jobs',{'running':True})
    _,agent=migrated.registry.resolve('jobs')
    calls=[]
    with worker_context(agent,migrated.controls.allowed) as context:
        context.require('jobs.execute')
        change(migrated,Node('component','jobs-worker'),Mode.DISABLED)
        calls.append('already guarded operation completed')
        with pytest.raises(PermissionError):context.require('jobs.execute')
    assert len(calls)==1


def test_concurrent_transition_revalidates_under_write_lock(migrated):
    from concurrent.futures import ThreadPoolExecutor
    controls=migrated.services.controls
    preview=controls.preview(Node('component','jobs-worker'),Mode.DISABLED)
    def apply(_):
        try:
            controls.transition(preview,actor='test',reason='concurrent test')
            return 'APPLIED'
        except StaleTransition:
            return 'STALE'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(apply,range(2)))==['APPLIED','STALE']


def test_catalog_revision_change_invalidates_preview(migrated):
    from capabilities.registry import CapabilityRegistry
    controls=migrated.services.controls
    preview=controls.preview(Node('component','jobs-worker'),Mode.DISABLED)
    updated=CapabilityRegistry(tuple(replace(c,version='1.0.1') if c.id=='jobs.worker' else c for c in controls.catalog.components.values()),
                               controls.catalog.capabilities.values())
    other=ComponentControls(migrated.store,updated,supported=controls.supported,guarded_consumers=controls.guarded_consumers)
    with pytest.raises(StaleTransition):other.transition(preview,actor='test',reason='changed catalog')
