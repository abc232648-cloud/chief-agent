from __future__ import annotations
import os
from pathlib import Path
from datetime import datetime, timezone
from database.store import Store
from .full_audit import FullAuditReport
from .channels import ConfiguredSmtpEmailChannel
from database.store_extensions import add_audit


def send_daily_full_audit(store: Store, *, outbox=None):
    if outbox is None:
        base=Path(os.environ.get('CHIEF_STATE_ROOT',str(store.path.resolve().parent)))
        outbox=base/'notifications'/'outbox'
    report=FullAuditReport(store, Path(outbox).parent.parent/'logs')
    day=datetime.now(timezone.utc).date().isoformat()
    path=report.write_day(day)
    text=path.read_text(encoding='utf-8')
    # TXT delivery is intentionally separate from the dashboard summary.
    local=Path(outbox); local.mkdir(parents=True,exist_ok=True)
    txt=local/f'full_audit_{day}.txt'; txt.write_text(text,encoding='utf-8')
    try:
        channel = ConfiguredSmtpEmailChannel.from_environment()
        channel._send(f'Job Worker — Full System Audit — {day}', text)
        email_status='SENT'
    except ValueError:
        email_status='NOT_CONFIGURED'
    except Exception as exc:
        email_status='FAILED'
        add_audit(store,'notification','Daily full-system audit email failed',status='FAILED',details=str(exc),data={'date':day})
    add_audit(store,'notification','Generated daily full-system audit',details=str(txt),data={'email_status':email_status,'date':day})
    return {'txt_path':str(txt),'email_status':email_status}

if __name__=='__main__':
    print(send_daily_full_audit(Store()))
