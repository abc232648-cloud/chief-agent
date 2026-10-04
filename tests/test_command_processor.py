import json
from database.store import Store
from worker.command_processor import CommandProcessor

class FakeGateway:
    def __init__(self, text): self.text=text
    def generate(self, request):
        class R: pass
        r=R();r.text=self.text;return r

def test_command_low_risk(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    cid=store.queue_command('find jobs')
    plan={'summary':'Find jobs','actions':[{'action':'discover_jobs','reason':'read-only','payload':{'urls':['https://example.test/jobs']}}]}
    class Browser:
        def discover(self,urls,preferences,facts):return {'status':'COMPLETED','jobs':[],'errors':[],'quarantined':[]}
    out=CommandProcessor(store, FakeGateway(json.dumps(plan)),browser_worker=Browser()).process_command(cid,'find jobs')
    assert out['approvals']==[]
    assert store.commands(1)[0]['status']=='COMPLETED'

def test_command_high_impact_creates_approval(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    cid=store.queue_command('apply')
    plan={'summary':'Apply','actions':[{'action':'submit_application','reason':'submit application','payload':{'job_id':'x'}}]}
    out=CommandProcessor(store, FakeGateway(json.dumps(plan))).process_command(cid,'apply')
    assert len(out['approvals'])==1
    assert store.commands(1)[0]['status']=='WAITING_APPROVAL'
    assert store.actions()[0]['status']=='PENDING'

def test_command_forbidden_blocked(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    cid=store.queue_command('give password')
    plan={'summary':'bad','actions':[{'action':'enter_password','reason':'bad','payload':{}}]}
    out=CommandProcessor(store, FakeGateway(json.dumps(plan))).process_command(cid,'give password')
    assert out['results'][0]['status']=='BLOCKED'

def test_approved_fill_action_executes_only_after_explicit_approval(tmp_path):
    from worker.application_executor import ApplicationExecutor
    from database.store import Store
    s=Store(tmp_path/'db.sqlite')
    s.add_source({'id':'linkedin','name':'LinkedIn','url':'https://linkedin.com','kind':'platform','protocol':'HTTPS','verification_status':'APPROVED'})
    fid=s.add_candidate_fact({'text':'Email: user@example.com','status':'USER_CONFIRMED','source_type':'user','source_id':'manual'})
    calls=[]
    ex=ApplicationExecutor(s, browser_factory=lambda u,f: calls.append((u,f)) or {'status':'FILLED'})
    aid=s.add_action('Fill form','fill_application_form','approved',payload={'url':'https://linkedin.com/jobs/1','fields':[{'label':'Email','input_type':'email','value':'user@example.com','fact_id':fid,'selector':'#email'}]})
    assert s.actions()[0]['status']=='PENDING'
    s.resolve_action(aid,'APPROVED')
    out=CommandProcessor(s,None,application_executor=ex).process_approved_action(aid)
    assert out['status']=='FILLED'
    assert s.get_action(aid)['status']=='DONE'
    assert calls
