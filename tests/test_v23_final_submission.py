import json
from database.store import Store
from worker.command_processor import CommandProcessor
from worker.final_submission import FinalSubmissionExecutor, canonical_form_hash

class FakeGateway:
    def generate(self, request):
        class R: pass
        r=R(); r.text='{}'; return r

def setup_app(s):
    s.add_job({'id':'j1','title':'SOC Analyst','company':'Acme','url':'https://acme.example/jobs/1'})
    s.add_application('a1','j1',status='DRAFT')
    s.add_source({'id':'acme','name':'Acme','url':'https://acme.example','kind':'company','protocol':'HTTPS','verification_status':'APPROVED'})
    fid=s.add_candidate_fact({'text':'Email: user@example.com','status':'USER_CONFIRMED','source_type':'user','source_id':'manual'})
    fields=[{'label':'Email','input_type':'email','value':'user@example.com','fact_id':fid,'selector':'#email'}]
    s.add_application_snapshot({'application_id':'a1','stage':'FORM_FILLED','source_url':'https://acme.example/apply/1','form_fields':fields})
    return fields

def test_preflight_requires_form_filled_and_approved_source(tmp_path):
    s=Store(tmp_path/'db.sqlite')
    ex=FinalSubmissionExecutor(s)
    assert ex.preflight('missing','https://acme.example/apply').ok is False

def test_submission_requires_approval(tmp_path):
    s=Store(tmp_path/'db.sqlite'); fields=setup_app(s)
    ex=FinalSubmissionExecutor(s,browser_factory=lambda u,sel:{'status':'SUBMITTED'})
    from worker.action_gate import ApprovalRequired
    try: ex.submit('a1','https://acme.example/apply/1','#submit',expected_form_hash=canonical_form_hash(fields))
    except ApprovalRequired: pass
    else: raise AssertionError('approval required')

def test_submission_records_receipt_and_blocks_duplicate(tmp_path):
    s=Store(tmp_path/'db.sqlite'); fields=setup_app(s); calls=[]
    ex=FinalSubmissionExecutor(s,browser_factory=lambda u,sel:calls.append((u,sel)) or {'status':'SUBMITTED','url_after':u,'title_after':'Thanks'})
    h=canonical_form_hash(fields)
    out=ex.submit('a1','https://acme.example/apply/1','#submit',approved=True,expected_form_hash=h)
    assert out['status']=='SUBMITTED'
    assert s.application_detail('a1')['status']=='SUBMITTED'
    out2=ex.submit('a1','https://acme.example/apply/1','#submit',approved=True,expected_form_hash=h)
    assert out2['status']=='BLOCKED'
    assert len(calls)==1

def test_changed_form_hash_blocks_submission(tmp_path):
    s=Store(tmp_path/'db.sqlite'); fields=setup_app(s)
    ex=FinalSubmissionExecutor(s,browser_factory=lambda u,sel:{'status':'SUBMITTED'})
    out=ex.submit('a1','https://acme.example/apply/1','#submit',approved=True,expected_form_hash='wrong')
    assert out['status']=='BLOCKED' and 'no longer matches' in out['reason']
