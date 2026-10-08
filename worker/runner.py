from __future__ import annotations
import os

from config.runtime import load_environment

load_environment()

from database.store import Store
from gateway.main import build_gateway
from .command_processor import CommandProcessor
from domains.runtime import DomainRuntime
from monitor_runner import queue_due_monitoring
from database.store_extensions import add_audit


def run_once(processor: CommandProcessor) -> bool:
    command = processor.controls.claim_command() if hasattr(processor,'controls') else processor.store.next_queued_command()
    if not command:
        return False
    processor.process_command(command["id"], command["instruction"])
    return True


def run_approved_once(processor: CommandProcessor) -> bool:
    if hasattr(processor,'controls') and not processor.controls.allowed('jobs'):return False
    action = processor.store.next_approved_action()
    if not action:
        return False
    try:
        result = processor.process_approved_action(int(action["id"]), claimed=True)
        add_audit(processor.store, "worker", "Processed approved action", status=str(result.get("status", "COMPLETED")), data={"action_id": action["id"]})
    except Exception as exc:
        add_audit(processor.store, "error", "Approved action processing failed", status="FAILED", details=str(exc), data={"action_id": action["id"]})
    return True


def main() -> int:
    from deployment.instance import load_instance
    from .ownership import WorkerOwnership
    instance=load_instance('worker')
    with WorkerOwnership(instance.database):
        from deployment.service_runtime import ServiceRuntime
        with ServiceRuntime(instance,'worker') as runtime:
            return _owned_main(instance.open_store(),runtime.stopping,runtime.ready)


def _owned_main(store, stopping=None, ready=lambda:None) -> int:
    # F services must not start in an anonymous/pre-migration compatibility mode.
    from identity.service import IdentityService
    IdentityService(store).require_ready()
    # A crash may follow an external side effect; require review before any retry.
    store.reset_executing_actions()
    gateway = build_gateway(store=store)
    from application.composition import default_registry
    registry = default_registry()
    from application.control_services import compose_control_services
    services = compose_control_services(store)
    processor = DomainRuntime(store, gateway, registry=registry, components=services.controls)
    processor.controls.startup()
    from threading import Thread,Event,current_thread,main_thread
    import signal
    stopping=stopping if stopping is not None else Event();old_handlers={}
    if current_thread() is main_thread():
        for signum in (signal.SIGTERM,signal.SIGINT):
            old_handlers[signum]=signal.getsignal(signum)
            signal.signal(signum,lambda *_:stopping.set())

    def heartbeat():
        while not stopping.is_set():
            processor.controls.heartbeat();stopping.wait(5)

    def push_delivery():
        from notifications.web_push import dispatch_pending
        while not stopping.is_set():
            try:
                # Notification transport is deliberately isolated from Job execution.
                # Provider/network failures retain durable cursors and retry later.
                dispatch_pending(store)
            except Exception:
                # Do not turn a notification-channel problem into worker failure or
                # leak provider exception details into the operational audit trail.
                pass
            stopping.wait(5)

    heartbeat_thread=Thread(target=heartbeat,daemon=True,name='chief-heartbeat')
    push_thread=Thread(target=push_delivery,daemon=True,name='chief-job-webpush')
    try:
        heartbeat_thread.start();push_thread.start()
        poll = float(os.environ.get("WORKER_POLL_SECONDS", "2"))
        if not 0.1<=poll<=60:raise ValueError('Worker poll interval must be between 0.1 and 60 seconds.')
        store.set_worker("IDLE", "Worker is running")
        ready()
        while not stopping.is_set():
            try:
                did_work = run_approved_once(processor)
                if not did_work and not stopping.is_set():did_work=run_once(processor)
                if not did_work:stopping.wait(poll)
            except KeyboardInterrupt:stopping.set()
            except Exception:
                add_audit(store, "error", "Worker loop error", status="FAILED", details='Worker operation failed; private exception content was not logged.')
                store.set_worker("ERROR", "Worker operation failed")
                stopping.wait(poll)
        return 0
    finally:
        stopping.set()
        if heartbeat_thread.is_alive():heartbeat_thread.join(timeout=12)
        if push_thread.is_alive():push_thread.join(timeout=30)
        for signum,handler in old_handlers.items():signal.signal(signum,handler)
        store.set_worker("STOPPED", "Worker stopped; unresolved actions remain review-only")
        if heartbeat_thread.is_alive():
            # Do not hand ownership to a second worker while an old heartbeat can still write.
            heartbeat_thread.join()
        if push_thread.is_alive():
            # Push cursor persistence also writes Chief state; finish before releasing ownership.
            push_thread.join()


if __name__ == "__main__":
    raise SystemExit(main())
