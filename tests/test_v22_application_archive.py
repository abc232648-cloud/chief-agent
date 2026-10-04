import json
from database.store import Store


def test_application_archive_preserves_cv_and_form_history(tmp_path):
    s=Store(tmp_path/'db.sqlite')
    s.add_job({'id':'j1','title':'SOC Analyst','company':'Acme','url':'https://acme.example/jobs/1'})
    s.add_application('a1','j1',status='DRAFT',draft={'cv':{'headline':'SOC Analyst'},'cover_letter':'Hello'},cv_path='/cv/soc.pdf',cover_letter_path='/letters/a1.txt')
    s.add_application_snapshot({'application_id':'a1','stage':'DRAFT','cv_id':'cv1','cv_name':'SOC Analyst — Security','cv_variant':'junior-security','cv_path':'/cv/soc.pdf','cv_snapshot':{'headline':'SOC Analyst'},'cover_letter_text':'Hello','source_url':'https://acme.example/jobs/1'})
    s.add_application_snapshot({'application_id':'a1','stage':'FORM_FILLED','source_url':'https://acme.example/apply/1','form_fields':[{'label':'Email','input_type':'email','value':'user@example.com','fact_id':1}]})
    s.add_application_event('a1','DRAFT_CREATED',details='draft')
    s.add_application_event('a1','FORM_FILLED',status='FILLED',details='safe fields only')
    d=s.application_detail('a1')
    assert d['title']=='SOC Analyst' and d['company']=='Acme'
    assert d['snapshots'][1]['cv_name']=='SOC Analyst — Security'
    assert json.loads(d['snapshots'][0]['form_fields_json'])[0]['label']=='Email'
    assert {e['event_type'] for e in d['events']}=={'DRAFT_CREATED','FORM_FILLED'}
