"""Activity summaries built from recorded commands and approval state."""
import calendar
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from database.store import Store
from database.store_extensions import add_audit
from .models import ActionItem, PeriodSummary
from .render import render_summary_txt


def build_summary(store, period, now):
    if period not in {'daily','weekly','monthly'}:raise ValueError('Invalid summary period')
    start=now.replace(hour=0,minute=0,second=0,microsecond=0)
    if period=='weekly':start-=timedelta(days=start.weekday())
    elif period=='monthly':start=start.replace(day=1)
    bounds=(start.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),now.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
    with store._connect() as con:
        commands=[dict(r) for r in con.execute('SELECT * FROM commands WHERE processed_at BETWEEN ? AND ? ORDER BY id',bounds)]
        actions=[dict(r) for r in con.execute("SELECT * FROM actions WHERE status='PENDING' ORDER BY priority DESC,id")]
        resolved=[dict(r) for r in con.execute('SELECT * FROM actions WHERE resolved_at BETWEEN ? AND ? ORDER BY id',bounds)]
    return PeriodSummary(period=period,generated_at=now.isoformat(),
        completed=tuple(f"Command #{c['id']}: {c['instruction']}" for c in commands if c['status']=='COMPLETED'),
        pending_actions=tuple(ActionItem(a['name'],a['description'],'Awaiting your decision',priority=a['priority']) for a in actions),
        blocked_items=tuple(f"Command #{c['id']} — {c['status']}: {c['instruction']} — {c['result']}" for c in commands if c['status'] in {'FAILED','BLOCKED','PARTIAL','ERROR'}),
        notable_items=(f'Recorded activity from {start.isoformat()} to {now.isoformat()}. Pending approvals are the current backlog.',)+tuple(f"Action #{a['id']} — {a['status']}: {a['name']}" for a in resolved))


def summary_settings(store,changes=None,*,registry):
    from control.agents import AgentControls
    import json
    AgentControls(store,registry)
    with store._connect() as con:
        row=con.execute("SELECT value FROM control_state WHERE key='summary_settings'").fetchone()
        settings=json.loads(row[0]) if row else dict(daily=True,weekly=True,monthly=True)
        if changes is not None:
            if set(changes)-set(settings) or any(type(v) is not bool for v in changes.values()):raise ValueError('Summary switches must be true/false.')
            settings.update(changes);con.execute("INSERT OR REPLACE INTO control_state VALUES('summary_settings',?)",(json.dumps(settings),))
    return settings


def deliver_summaries(store,now,*,registry):
    from .channels import ConfiguredSmtpEmailChannel
    from .desktop import DesktopNotificationChannel
    output=[]
    for period,enabled in summary_settings(store,registry=registry).items():
        if not enabled or (period=='weekly' and now.weekday()!=6) or (period=='monthly' and now.day!=calendar.monthrange(now.year,now.month)[1]):continue
        identity=f'summary:{period}:{now.date().isoformat()}'
        if store.report_exists(identity):continue
        summary=build_summary(store,period,now)
        base=store.path.resolve().parent
        if base.name=='database':base=base.parent
        folder=base/'notifications/outbox';folder.mkdir(parents=True,exist_ok=True)
        path=folder/f'summary_{period}_{now.date().isoformat()}.txt';path.write_text(render_summary_txt(summary),encoding='utf-8');path.chmod(0o600)
        store.add_report(identity,str(path))
        store.add_notification(period.title()+' activity summary','Your recorded activity summary is ready in Reports.','INFO',domain='system',related_page='reports')
        channels=[('desktop',DesktopNotificationChannel())]
        try:channels.append(('email',ConfiguredSmtpEmailChannel.from_environment()))
        except ValueError:pass
        for name,channel in channels:
            try:status='UNAVAILABLE' if channel.send_summary(summary) is False else 'SENT'
            except Exception:status='FAILED'
            add_audit(store,'notification',period.title()+' summary: '+name,status=status)
        output.append(identity)
    return output


def load_demo_or_history(period):
    return build_summary(Store(os.getenv('JOB_WORKER_DB','database/worker.db')),period,datetime.now(timezone.utc))


def main():
    if len(sys.argv)!=2 or sys.argv[1] not in {'daily','weekly','monthly'}:
        print('Usage: python -m notifications.report daily|weekly|monthly');return 2
    from .channels import LocalInboxChannel
    LocalInboxChannel().send_summary(load_demo_or_history(sys.argv[1]));return 0

if __name__=='__main__':raise SystemExit(main())
