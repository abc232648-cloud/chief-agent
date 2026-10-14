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

    def __init__(self, store: Store, poll_seconds: float | None = None, *, registry, dependency_checker=None):
        self.store = store
        self.registry = registry
        self.poll_seconds = float(poll_seconds or os.getenv("SCHEDULER_POLL_SECONDS", "30"))
        self._timezone_override = os.getenv("JOB_WORKER_TIMEZONE")
        if self._timezone_override:
            ZoneInfo(self._timezone_override)
        self.audit_hour = int(os.getenv("FULL_AUDIT_HOUR", "23"))
        self.audit_minute = int(os.getenv("FULL_AUDIT_MINUTE", "55"))
        self._last_audit_date: str | None = None
        if dependency_checker is None:
            from update_center.dependency_check import check_if_due
            self.dependency_checker = check_if_due
            self._default_dependency_checker = True
        else:
            self.dependency_checker = dependency_checker
            self._default_dependency_checker = False

    @property
    def timezone(self):
        from operations.local_timezone import host_zone
        return ZoneInfo(self._timezone_override) if self._timezone_override else host_zone()

    def local_now(self) -> datetime:
        return datetime.now(timezone.utc).astimezone(self.timezone)

    def run_due_once(self) -> dict:
        from control.agents import AgentControls
        AgentControls(self.store, self.registry)
        return run_guarded_cycle(self.store, self._run_due_once)

    def _dependency_update_check(self) -> dict:
        # Test suites exercise discovery directly with deterministic metadata. Do
        # not introduce live network dependency into unrelated repository tests.
        if self._default_dependency_checker and os.getenv("CHIEF_INSTANCE_MODE", "").lower() == "test":
            return {"status": "SKIPPED_TEST_MODE", "updates": 0, "new_updates": 0, "errors": 0}
        try:
            result = self.dependency_checker(
                Path(__file__).resolve().parent,
                self.store.path.resolve().parent,
                environ=os.environ,
            )
        except Exception:
            add_audit(
                self.store,
                "scheduler",
                "Dependency update discovery requires review",
                status="REVIEW",
                details="Update metadata check failed; no dependency was changed and automatic retry/activation is prohibited.",
            )
            return {"status": "REVIEW", "updates": 0, "new_updates": 0, "errors": 1}

        summary = {
            "status": result["status"],
            "cadence_hours": result.get("cadence_hours"),
            "updates": len(result.get("updates", ())),
            "new_updates": len(result.get("new_updates", ())),
            "errors": int(result.get("errors", 0)),
        }
        if summary["new_updates"]:
            self.store.add_notification(
                "Dependency updates available",
                f"{summary['new_updates']} new dependency update(s) were discovered. Review qualification evidence before any change.",
                severity="INFO",
                related_page="updates",
            )
        if result["status"] != "NOT_DUE":
            add_audit(
                self.store,
                "scheduler",
                "Dependency update discovery completed",
                data=summary,
                status="REVIEW" if summary["errors"] else "OK",
            )
        return summary

    def _run_due_once(self) -> dict:
        from domains.storage import deliver_due_reminders
        from control.agents import AgentControls
        controls = AgentControls(self.store, self.registry)
        deliver_due_reminders(self.store, controls)
        from control.schedules import deliver
        general_reminders=deliver(self.store,controls)
        queued = _queue_due_monitoring(self.store, controls)
        dependency_updates = self._dependency_update_check()
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
        return {"general_reminders":general_reminders,"monitoring_commands": queued, "dependency_updates":dependency_updates, "audit": audit_result, "summaries":summaries,"local_time": now.isoformat()}

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
