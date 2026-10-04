from __future__ import annotations
from database.store import Store
from monitoring import seed_monitoring, due_tasks, mark_run
from database.store_extensions import add_audit


def queue_due_monitoring(store: Store, controls):
    from operations.scheduler_cycle import run_guarded_cycle
    return run_guarded_cycle(store, lambda: _queue_due_monitoring(store, controls))


def _queue_due_monitoring(store: Store, controls):
    """Internal cycle body; supported callers enter through the guarded wrapper."""
    if not controls.allowed('jobs'):return []
    seed_monitoring(store); queued=[]
    for task in due_tasks(store):
        if task['task_type']=='job_market_refresh':
            cid=store.queue_command('Refresh the job market: discover and analyze strong remote cybersecurity opportunities using approved sources. Also recommend useful new job sources when there is a concrete reason; explain their value for my search. Do not visit new sources until I authorize access.')
        elif task['task_type']=='profile_refresh':
            cid=store.queue_command('Check my enabled authorized profile links for relevant professional/profile updates. Do not log in, request credentials, or use OTPs.')
        else:
            continue
        mark_run(store,task['id'],task['interval_days'],'QUEUED'); add_audit(store,'monitoring','Queued periodic monitoring task',data={'task_id':task['id'],'task_type':task['task_type'],'command_id':cid}); queued.append(cid)
    return queued

def main():
    from application.composition import default_registry
    from control.agents import AgentControls
    from deployment.instance import load_instance
    from identity.service import IdentityService
    from worker.ownership import ComponentOwnership
    instance = load_instance('scheduler')
    with ComponentOwnership(instance.database, 'scheduler'):
        store = instance.open_store()
        IdentityService(store).require_ready()
        print(queue_due_monitoring(store, AgentControls(store, default_registry())))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
