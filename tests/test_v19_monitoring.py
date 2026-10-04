from control.agents import AgentControls
from application.composition import default_registry
from database.store import Store
from monitoring import seed_monitoring, due_tasks
from monitor_runner import queue_due_monitoring

def test_monitoring_seeds_and_queues(tmp_path):
    s=Store(tmp_path/'db.sqlite'); seed_monitoring(s)
    assert {x['task_type'] for x in s.monitoring_tasks()} == {'job_market_refresh','profile_refresh'}
    ids=queue_due_monitoring(s, AgentControls(s, default_registry())); assert len(ids)==2
    assert len(s.commands())==2
    assert not due_tasks(s)
