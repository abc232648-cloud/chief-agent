import json
from database.store import Store
from candidate.fact_store import confirmed_facts
from skills.application_generation.skill import generate_application_draft

class FakeGateway:
    def __init__(self, text): self.text=text
    def generate(self, request):
        class R: pass
        r=R(); r.text=self.text; r.provider='fake'; r.model='fake'; return r

def payload(state='USER_CONFIRMED'):
    return {"cv":{"headline":"SOC Analyst","summary":"","skills":["Nmap"],"experience":[],"education":[],"links":[]},"cover_letter":"I am interested in this role. Nmap Thank you for considering my application.","claims":[{"text":"Nmap","evidence_state":state,"fact_id":1}]}

def test_uploaded_cv_fact_is_proposed_not_confirmed(tmp_path):
    s=Store(tmp_path/'db.sqlite')
    fid=s.add_candidate_fact({'text':'Wazuh experience','status':'PROPOSED','source_type':'uploaded_cv','source_id':'cv1'})
    assert s.candidate_facts()[0]['status']=='PROPOSED'
    assert confirmed_facts({'facts': s.candidate_facts()})['facts']==[]
    s.update_candidate_fact(fid,'USER_CONFIRMED')
    assert confirmed_facts({'facts': s.candidate_facts()})['facts'][0]['text']=='Wazuh experience'

def test_confirmed_fact_survives_cv_removal_until_user_revokes(tmp_path):
    s=Store(tmp_path/'db.sqlite')
    fid=s.add_candidate_fact({'text':'Nmap','status':'USER_CONFIRMED','source_type':'uploaded_cv','source_id':'cv1'})
    assert confirmed_facts({'facts': s.candidate_facts()})['facts']
    # CV deletion is intentionally unrelated to fact state.
    assert s.candidate_facts()[0]['status']=='USER_CONFIRMED'
    s.update_candidate_fact(fid,'REVOKED')
    assert confirmed_facts({'facts': s.candidate_facts()})['facts']==[]

def test_application_generation_receives_only_user_confirmed_facts():
    out,_=generate_application_draft(FakeGateway(json.dumps(payload())), {'title':'SOC Analyst'}, {'facts':[{'id':1,'text':'Nmap','status':'USER_CONFIRMED'},{'text':'Wazuh','status':'PROPOSED'}]})
    assert out['claims'][0]['text']=='Nmap'
