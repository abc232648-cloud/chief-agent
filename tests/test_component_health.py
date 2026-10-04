from datetime import datetime,timezone,timedelta
import pytest
from checkpoint_c_fixture import migrated,unmigrated
from capabilities.contracts import Node,Mode
from control.health import SystemHealth
from control.health_contracts import HealthStatus as H,Observation

NOW=datetime(2026,9,24,12,tzinfo=timezone.utc)


def observation(status=H.HEALTHY,source='2026-09-24T12:00:00Z',receipt='2026-09-24T12:00:00Z'):
    return Observation(status,source,receipt,'synthetic-probe','Synthetic observation')


def full_probes(controls):
    return {node:lambda:observation() for node in controls.catalog.graph.nodes}


def test_desired_disabled_does_not_fake_health_or_mutate_mode(migrated):
    controls=migrated.services.controls;node=Node('component','jobs-worker')
    controls.transition(controls.preview(node,Mode.DISABLED),actor='test',reason='test')
    health=SystemHealth(controls,probes={},now=lambda:NOW)
    result=next(r for r in health.snapshot() if r.node==node)
    assert result.desired_mode=='DISABLED' and result.intentionally_suppressed
    assert result.status==H.UNKNOWN
    assert controls.desired(node).revision==1
    probes=full_probes(controls);probes[node]=lambda:observation(H.DEGRADED)
    result=next(r for r in SystemHealth(controls,probes=probes,now=lambda:NOW).snapshot() if r.node==node)
    assert result.status==H.DEGRADED and result.desired_mode=='DISABLED'


@pytest.mark.parametrize('source,receipt',[(None,'2026-09-24T12:00:00Z'),
    ('bad','2026-09-24T12:00:00Z'),('2026-09-24T11:59:00Z','2026-09-24T12:00:00Z'),
    ('2026-09-24T12:01:00Z','2026-09-24T12:00:00Z'),('2026-09-24T12:00:00Z','2026-09-24T12:01:00Z'),
    ('2026-09-24 12:00:00','2026-09-24T12:00:00Z')])
def test_unusable_timestamps_remain_unknown(migrated,source,receipt):
    node=Node('component','chief.runtime')
    health=SystemHealth(migrated.services.controls,probes={node:lambda:observation(source=source,receipt=receipt)},now=lambda:NOW)
    result=next(r for r in health.snapshot() if r.node==node)
    assert result.status==H.UNKNOWN


@pytest.mark.parametrize('status',[H.UNKNOWN,H.HEALTHY,H.DEGRADED,H.UNAVAILABLE])
def test_dependency_health_propagation(migrated,status):
    controls=migrated.services.controls;probes=full_probes(controls)
    probes[Node('component','chief.database')]=lambda:observation(status)
    results={r.node:r for r in SystemHealth(controls,probes=probes,now=lambda:NOW).snapshot()}
    assert results[Node('capability','jobs.execute')].status==status
    assert results[Node('capability','jobs.execute')].safe_degradation is None


@pytest.mark.parametrize('value',['1','nan','999999999999999999999999','bad',str(NOW.timestamp()+60)])
def test_legacy_heartbeat_stale_invalid_future_is_unknown(migrated,value):
    with migrated.store._connect() as con:con.execute("UPDATE control_state SET value=? WHERE key='heartbeat'",(value,))
    health=SystemHealth(migrated.services.controls,now=lambda:NOW)
    assert next(r for r in health.snapshot() if r.node.id=='chief.runtime').status==H.UNKNOWN


def test_fresh_heartbeat_does_not_qualify_browser_or_folio(migrated):
    with migrated.store._connect() as con:con.execute("UPDATE control_state SET value=? WHERE key='heartbeat'",(str(NOW.timestamp()),))
    health=SystemHealth(migrated.services.controls,now=lambda:NOW)
    results={r.node.id:r for r in health.snapshot()}
    assert results['chief.runtime'].status==H.HEALTHY
    assert results['chief.browser'].status==H.UNKNOWN
    assert results['jobs.execute'].status==H.UNKNOWN


def test_probe_exception_stays_unknown(migrated):
    node=Node('component','chief.runtime')
    def fail():raise RuntimeError('synthetic')
    results=SystemHealth(migrated.services.controls,probes={node:fail},now=lambda:NOW).snapshot()
    assert next(r for r in results if r.node==node).status==H.UNKNOWN
