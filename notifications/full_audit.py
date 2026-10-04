from __future__ import annotations
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from database.store import Store

class FullAuditReport:
    """Daily whole-system audit, not just browser activity."""
    def __init__(self, store: Store, log_dir: str | Path = 'logs'):
        self.store=store; self.log_dir=Path(log_dir); self.log_dir.mkdir(parents=True, exist_ok=True)

    def render_day(self, day: str | None = None) -> str:
        day = day or datetime.now(timezone.utc).date().isoformat()
        d=date.fromisoformat(day); rows=self.store.audit(limit=5000, start=day+' 00:00:00', end=(d+timedelta(days=1)).isoformat()+' 00:00:00')
        lines=[f'JOB WORKER — FULL SYSTEM AUDIT — {day}','='*58,'','This is the complete system activity log, not only browser activity.','']
        if not rows: lines.append('No recorded system events.')
        for r in reversed(rows):
            lines.append(f"[{r['event_time']}] [{r['category']}] [{r['actor']}] {r['action']} — {r['status']}")
            if r['details']: lines.append(f"  {r['details']}")
            if r['data_json']!='{}': lines.append(f"  DATA: {r['data_json']}")
        return '\n'.join(lines)+'\n'

    def write_day(self, day: str | None = None) -> Path:
        day=day or datetime.now(timezone.utc).date().isoformat(); p=self.log_dir/f'audit_{day}.txt'; p.write_text(self.render_day(day),encoding='utf-8'); return p
