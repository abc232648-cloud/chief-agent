from copy import deepcopy
import pytest
from skills.application_generation.skill import validate_evidence

FACTS={'facts':[{'id':7,'text':'I am not CISSP certified.','status':'USER_CONFIRMED'}]}


def draft():
    text=FACTS['facts'][0]['text']
    return {'cv':{'headline':'SOC Analyst','summary':text,'skills':[], 'experience':[], 'education':[text],'links':[]},
            'cover_letter':'I am interested in this role. '+text+' Thank you for considering my application.',
            'claims':[{'text':text,'evidence_state':'USER_CONFIRMED','fact_id':7}]}


def test_literal_confirmed_evidence_passes():
    assert validate_evidence(draft(),FACTS,{'title':'SOC Analyst'})


def test_form_cannot_remove_negation_from_fact(tmp_path):
    from database.store import Store
    from worker.application_executor import ApplicationExecutor
    store=Store(tmp_path/'db')
    fid=store.add_candidate_fact({'text':'I am not CISSP certified.','status':'USER_CONFIRMED'})
    assert ApplicationExecutor(store).validate_fields([{'label':'Certification','input_type':'text','value':'CISSP certified.','fact_id':fid}])


@pytest.mark.parametrize('attack',['wrong_id','self_verified','negation','hidden_summary','hidden_skill','hidden_letter','revoked'])
def test_fabrications_are_rejected(attack):
    value=draft(); facts=deepcopy(FACTS)
    if attack=='wrong_id':value['claims'][0]['fact_id']=999
    if attack=='self_verified':value['claims'][0]['evidence_state']='VERIFIED'
    if attack=='negation':value['claims'][0]['text']='I am CISSP certified.'
    if attack=='hidden_summary':value['cv']['summary']='Ten years of experience'
    if attack=='hidden_skill':value['cv']['skills']=['CISSP']
    if attack=='hidden_letter':value['cover_letter']+=' I led a team of 20.'
    if attack=='revoked':facts['facts'][0]['status']='REVOKED'
    with pytest.raises(ValueError):validate_evidence(value,facts,{'title':'SOC Analyst'})
