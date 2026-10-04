from database.store import Store
from worker.application_executor import ApplicationExecutor
from worker.action_gate import PolicyGate, ApprovalRequired


def test_fill_requires_approval(tmp_path):
    s=Store(tmp_path/'db.sqlite')
    fid=s.add_candidate_fact({'text':'Email: user@example.com','status':'USER_CONFIRMED','source_type':'user','source_id':'manual'})
    ex=ApplicationExecutor(s)
    fields=[{'label':'Email','input_type':'email','value':'user@example.com','fact_id':fid,'selector':'#email'}]
    try:
        ex.prepare('https://linkedin.com/jobs/1', fields)
    except ApprovalRequired:
        pass
    else:
        raise AssertionError('approval was not required')


def test_password_field_is_blocked_even_after_approval(tmp_path):
    s=Store(tmp_path/'db.sqlite')
    fid=s.add_candidate_fact({'text':'Password: nope','status':'USER_CONFIRMED','source_type':'user','source_id':'manual'})
    ex=ApplicationExecutor(s)
    fields=[{'label':'Password','input_type':'text','value':'nope','fact_id':fid,'selector':'#password'}]
    out=ex.prepare('https://linkedin.com/jobs/1', fields, approved=True)
    assert out['status']=='BLOCKED'


def test_unconfirmed_fact_cannot_be_used(tmp_path):
    s=Store(tmp_path/'db.sqlite')
    fid=s.add_candidate_fact({'text':'Email: user@example.com','status':'PROPOSED','source_type':'uploaded_cv','source_id':'cv1'})
    ex=ApplicationExecutor(s)
    fields=[{'label':'Email','input_type':'email','value':'user@example.com','fact_id':fid,'selector':'#email'}]
    out=ex.prepare('https://linkedin.com/jobs/1', fields, approved=True)
    assert out['status']=='BLOCKED'


def test_approved_browser_factory_is_used(tmp_path):
    s=Store(tmp_path/'db.sqlite')
    fid=s.add_candidate_fact({'text':'Email: user@example.com','status':'USER_CONFIRMED','source_type':'user','source_id':'manual'})
    calls=[]
    def fake(url,fields): calls.append((url,fields)); return {'status':'FILLED'}
    s.add_source({'id':'linkedin','name':'LinkedIn','url':'https://linkedin.com','kind':'platform','protocol':'HTTPS','verification_status':'APPROVED'})
    ex=ApplicationExecutor(s,browser_factory=fake)
    fields=[{'label':'Email','input_type':'email','value':'user@example.com','fact_id':fid,'selector':'#email'}]
    out=ex.prepare('https://linkedin.com/jobs/1', fields, approved=True)
    assert out['status']=='FILLED' and calls

def test_application_domain_requires_approved_source(tmp_path):
    s=Store(tmp_path/'db.sqlite')
    fid=s.add_candidate_fact({'text':'Email: user@example.com','status':'USER_CONFIRMED','source_type':'user','source_id':'manual'})
    ex=ApplicationExecutor(s, browser_factory=lambda u,f:{'status':'FILLED'})
    fields=[{'label':'Email','input_type':'email','value':'user@example.com','fact_id':fid,'selector':'#email'}]
    out=ex.prepare('https://linkedin.com/jobs/1', fields, approved=True)
    assert out['status']=='REVIEW'
    s.add_source({'id':'linkedin','name':'LinkedIn','url':'https://linkedin.com','kind':'platform','protocol':'HTTPS','verification_status':'APPROVED'})
    out=ex.prepare('https://linkedin.com/jobs/1', fields, approved=True)
    assert out['status']=='FILLED'
