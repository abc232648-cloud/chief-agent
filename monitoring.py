from __future__ import annotations
from datetime import datetime, timezone, timedelta
from database.store import Store
from notifications.full_audit import FullAuditReport

JOB_MARKET_TASK='job_market_refresh'
PROFILE_REFRESH_TASK='profile_refresh'

def seed_monitoring(store: Store):
    existing={x['task_type'] for x in store.monitoring_tasks()}
    if JOB_MARKET_TASK not in existing:
        store.add_monitoring_task({'task_type':JOB_MARKET_TASK,'interval_days':1,'notes':'Periodically refresh job-market discovery.'})
    if PROFILE_REFRESH_TASK not in existing:
        store.add_monitoring_task({'task_type':PROFILE_REFRESH_TASK,'interval_days':7,'notes':'Periodically check enabled user profile links; never enter credentials automatically.'})

def due_tasks(store: Store, now=None):
    now=now or datetime.now(timezone.utc); result=[]
    for task in store.monitoring_tasks():
        if not task['enabled']: continue
        if not task['next_due_at'] or datetime.fromisoformat(task['next_due_at'].replace('Z','+00:00')) <= now: result.append(task)
    return result

def mark_run(store: Store, task_id: int, interval_days: int, status='QUEUED'):
    now=datetime.now(timezone.utc); nxt=now+timedelta(days=interval_days)
    with store._connect() as con:
        con.execute('UPDATE monitoring_tasks SET last_run_at=?,next_due_at=?,status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(now.isoformat(),nxt.isoformat(),status,task_id))
