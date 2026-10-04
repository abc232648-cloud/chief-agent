from __future__ import annotations

import os
import time
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from config.runtime import load_environment

load_environment()

from database.store import Store
from database.store_extensions import add_audit
from monitor_runner import _queue_due_monitoring
from notifications.daily_full_audit import send_daily_full_audit


class AutonomousScheduler:
    """Local, dependency-free scheduler for recurring worker duties.

    It deliberately uses polling rather than a cloud scheduler. All work is still
    represented as normal commands/actions in SQLite and therefore follows the
    existing AI -> Policy Gate -> worker path.
    """

    def __init__(self, store: Store, poll_seconds: float | None = None, *, registry):
        self.store = store
        self.registry = registry
        self.poll_seconds = float(poll_seconds or os.getenv("SCHEDULER_POLL_SECONDS", "30"))
        self.timezone = ZoneInfo(os.getenv("JOB_WORKER_TIMEZONE", "Africa/Lagos"))
        self.audit_hour = int(os.getenv("FULL_AUDIT_HOUR", "23"))
        self.audit_minute = int(os.getenv("FULL_AUDIT_MINUTE", "55"))
        self._last_audit_date: str | None = None

    def local_now(self) -> datetime:
        return datetime.now(timezone.utc).astimezone(self.timezone)

    def run_due_once(self) -> dict:
        from control.agents import AgentControls
        AgentControls(self.store, self.registry)
        return run_guarded_cycle(self.store, self._run_due_once)

    def _run_due_once(self) -> dict:
        from domains.storage import deliver_due_reminders
        from control.agents import AgentControls
        controls = AgentControls(self.store, self.registry)
        deliver_due_reminders(self.store, controls)
        from control.schedules import deliver
        general_reminders=deliver(self.store,controls)
        queued = _queue_due_monitoring(self.store, controls)
        audit_result = None
        now = self.local_now()
        day = now.date().isoformat()
        period = f'full_audit:{day}'
        if (now.hour, now.minute) >= (self.audit_hour, self.audit_minute) and not self.store.report_exists(period):
            audit_result = send_daily_full_audit(self.store)
            self.store.add_report(period, audit_result['txt_path'])
            self._last_audit_date = day
            add_audit(self.store, "scheduler", "Completed daily full-system audit delivery", data=audit_result)
        summaries=[]
        if (now.hour,now.minute)>=(self.audit_hour,self.audit_minute):
            from notifications.report import deliver_summaries
            summaries=deliver_summaries(self.store,now,registry=self.registry)
        return {"general_reminders":general_reminders,"monitoring_commands": queued, "audit": audit_result, "summaries":summaries,"local_time": now.isoformat()}

    def run_forever(self,stopping=None):
        from threading import Event
        stopping=stopping if stopping is not None else Event()
        if not 0.1<=self.poll_seconds<=300:raise ValueError('Scheduler poll interval must be between 0.1 and 300 seconds.')
        add_audit(self.store, "scheduler", "Autonomous scheduler started", data={"timezone": str(self.timezone)})
        while not stopping.is_set():
            try:
                self.run_due_once()
                stopping.wait(self.poll_seconds)
            except KeyboardInterrupt:
                add_audit(self.store, "scheduler", "Autonomous scheduler stopped", status="STOPPED")
                return
            except Exception:
                add_audit(self.store, "scheduler", "Scheduler stopped for review", status="REVIEW", details='Cycle incomplete; automatic replay prohibited.')
                raise SchedulerReviewRequired('Scheduler cycle requires review.') from None


from operations.scheduler_cycle import run_guarded_cycle, SchedulerReviewRequired


def main():
    from deployment.instance import load_instance
    from worker.ownership import ComponentOwnership
    from deployment.service_runtime import ServiceRuntime
    from application.composition import default_registry
    from identity.service import IdentityService
    instance=load_instance('scheduler')
    with ComponentOwnership(instance.database,'scheduler'):
        store=instance.open_store();IdentityService(store).require_ready()
        with ServiceRuntime(instance,'scheduler') as runtime:
            runtime.ready()
            AutonomousScheduler(store,registry=default_registry()).run_forever(runtime.stopping)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
