import base64
from database.store import Store
from notifications.full_audit import FullAuditReport

def test_cv_and_profiles(tmp_path):
    s=Store(tmp_path/'db.sqlite')
    p=tmp_path/'cv.txt'; p.write_text('truthful cv')
    s.add_cv({'id':'cv1','name':'SOC CV','role_type':'SOC','variant':'security','file_path':str(p),'file_type':'txt'})
    assert s.cvs()[0]['variant']=='security'
    s.set_cv_active('cv1',False); assert s.cvs()[0]['active']==0
    pid=s.add_profile_link({'label':'LinkedIn','url':'https://linkedin.com/in/example','profile_type':'LinkedIn'})
    assert s.profile_links()[0]['url'].startswith('https://')
    s.update_profile_link(pid,enabled=False); assert s.profile_links()[0]['enabled']==0

def test_full_audit_is_whole_system(tmp_path):
    s=Store(tmp_path/'db.sqlite')
    s.add_audit('command','Started command',details='Find jobs')
    s.add_audit('browser','Read page',details='https://example.com')
    out=FullAuditReport(s,tmp_path/'logs').write_day()
    text=out.read_text()
    assert 'Started command' in text and 'Read page' in text
