from database.store import Store
from candidate.cv_library import choose_cv, extract_text

def test_choose_cv_and_extract(tmp_path):
    s=Store(tmp_path/'db.sqlite'); p=tmp_path/'soc.txt'; p.write_text('SOC CV')
    s.add_cv({'id':'a','name':'General','role_type':'','variant':'general','file_path':str(p),'file_type':'txt'})
    p2=tmp_path/'soc2.txt'; p2.write_text('SOC CV 2')
    s.add_cv({'id':'b','name':'SOC Security','role_type':'SOC Analyst','variant':'security','file_path':str(p2),'file_type':'txt'})
    selected=choose_cv(s.cvs(),role='SOC Analyst',variant='security')
    assert selected['id']=='b'; assert extract_text(selected['file_path'])=='SOC CV 2'
