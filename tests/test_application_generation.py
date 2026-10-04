import json
from skills.application_generation.skill import generate_application_draft, validate_candidate_claims
from database.store import Store
from worker.command_processor import CommandProcessor

class FakeGateway:
    def __init__(self, text): self.text=text
    def generate(self, request):
        class R: pass
        r=R(); r.text=self.text; r.provider='fake'; r.model='fake'; return r

class SequenceGateway:
    def __init__(self, texts): self.texts=list(texts)
    def generate(self, request):
        class R: pass
        r=R(); r.text=self.texts.pop(0); r.provider='fake'; r.model='fake'; return r

def valid_payload():
    return {"cv":{"headline":"SOC Analyst","summary":"Nmap","skills":["Nmap"],"experience":[],"education":[],"links":[]},"cover_letter":"I am interested in this role. Nmap Thank you for considering my application.","claims":[{"text":"Nmap","evidence_state":"USER_CONFIRMED","fact_id":1}]}

def test_application_schema_and_truthful_claims():
    facts={"facts":[{"id":1,"text":"Nmap","status":"USER_CONFIRMED"}]}
    out,_=generate_application_draft(FakeGateway(json.dumps(valid_payload())), {"title":"SOC Analyst"}, facts)
    assert validate_candidate_claims(out)

def test_unverified_claim_is_rejected():
    bad=valid_payload(); bad["claims"][0]["evidence_state"]="INFERRED"
    try: validate_candidate_claims(bad)
    except ValueError: pass
    else: raise AssertionError("unverified claim accepted")

def test_draft_saved_to_store(tmp_path, monkeypatch):
    store=Store(tmp_path/'db.sqlite')
    store.add_candidate_fact({'text':'Nmap','status':'USER_CONFIRMED'})
    store.add_job({'id':'j1','title':'SOC Analyst','company':'Acme','url':'https://acme.test/jobs/1'})
    cid=store.queue_command('draft application')
    plan={"summary":"draft","actions":[{"action":"draft_cv","reason":"safe draft","payload":{"job_id":"j1","candidate_facts":{"facts":[{"text":"Nmap","evidence_state":"USER_CONFIRMED","fact_id":1}]}}}]}
    out=CommandProcessor(store, SequenceGateway([json.dumps(plan), json.dumps(valid_payload())])).process_command(cid,'draft application')
    assert out['approvals']==[]
    apps=store.applications(); assert apps and apps[0]['status']=='DRAFT'
