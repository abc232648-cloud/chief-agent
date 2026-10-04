"""Local, read-only observations. Missing probes stay unknown; no repair/failover."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from capabilities.contracts import Node, Mode
from operations.time_integrity import timestamp_status, utc_now, utc_text
from .health_contracts import HealthStatus as H, Observation, HealthResult


class SystemHealth:
    def __init__(self, controls, *, probes=None, now=utc_now, max_age=timedelta(seconds=30)):
        self.controls, self.now, self.max_age = controls, now, max_age
        if probes is not None:
            self.probes = dict(probes)
        else:
            from .local_probes import database, service
            self.probes = {
                Node('component', 'chief.runtime'): self.heartbeat,
                Node('component', 'chief.database'): lambda: database(controls.store, self.now),
                Node('component', 'chief.dashboard'): lambda: service(controls.store, 'dashboard', self.now),
                Node('component', 'chief.scheduler'): lambda: service(controls.store, 'scheduler', self.now),
            }

    def heartbeat(self):
        with self.controls.store._connect() as con:
            exists = con.execute("SELECT 1 FROM sqlite_master WHERE name='control_state'").fetchone()
            row = con.execute("SELECT value FROM control_state WHERE key='heartbeat'").fetchone() if exists else None
        observed = None
        try:
            if row:
                observed = utc_text(datetime.fromtimestamp(float(row[0]), timezone.utc))
        except (ValueError, OverflowError, OSError):
            pass
        return Observation(H.HEALTHY, observed, utc_text(self.now()), 'legacy-worker-heartbeat',
                           'Worker connectivity only; no browser, Folio or deployment qualification.')

    def snapshot(self):
        results = {}
        current = self.now()
        for node in self.controls.catalog.graph.topological_order():
            observation = None
            status, reason = H.UNKNOWN, 'No observed health probe.'
            if node in self.probes:
                try:
                    observation = self.probes[node]()
                    if not isinstance(observation, Observation) or not isinstance(observation.status, H):
                        raise ValueError('Invalid observation contract.')
                    source_freshness = timestamp_status(observation.observed_at, now=current, max_age=self.max_age)
                    receipt_freshness = timestamp_status(observation.received_at, now=current, max_age=self.max_age)
                    if source_freshness == receipt_freshness == 'CURRENT':
                        status, reason = observation.status, observation.reason
                    else:
                        reason = 'Unusable observation: source=' + source_freshness + ', receipt=' + receipt_freshness
                except Exception:
                    observation = None
                    reason = 'Local probe failed; no healthy result inferred.'
            deps = tuple((dep.key, results[dep].status) for dep in self.controls.catalog.graph.dependencies(node, transitive=False))
            states = {state for _, state in deps}
            if H.UNAVAILABLE in states:
                status, reason = H.UNAVAILABLE, 'A required dependency is unavailable; no verified fallback.'
            elif status != H.UNAVAILABLE and H.UNKNOWN in states:
                status, reason = H.UNKNOWN, 'A required dependency has unknown health.'
            elif status == H.HEALTHY and H.DEGRADED in states:
                status, reason = H.DEGRADED, 'A required dependency is degraded.'
            desired = self.controls.desired(node)
            legacy_paused = bool(desired.legacy_controls) and not dict(desired.legacy_controls).get('running')
            results[node] = HealthResult(node, status, reason, observation, deps, desired.mode.value,
                                         desired.mode != Mode.ENABLED or legacy_paused)
        return tuple(results[node] for node in sorted(results))

    def describe(self):
        return [asdict(result) for result in self.snapshot()]
