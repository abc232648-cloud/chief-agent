from database.store import Store
from notifications.full_audit import FullAuditReport

def test_full_audit_is_whole_system(tmp_path):
    s=Store(tmp_path/'db.sqlite')
    s.add_audit('command','Started command',details='Find jobs')
    s.add_audit('browser','Read page',details='https://example.com')
    row=s.audit(limit=1)[0]
    day=row['event_time'][:10]
    out=FullAuditReport(s,tmp_path/'logs').write_day(day)
    text=out.read_text(); assert 'Started command' in text and 'Read page' in text
