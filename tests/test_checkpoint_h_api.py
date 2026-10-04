import json
from datetime import timedelta
import pytest
from tests.test_identity_http import request
from tests.checkpoint_f_fixture import PASSWORD
from identity.service import IdentityService
from operations.time_integrity import utc_now,utc_text

SYSTEM=('capabilities','models','policies','runtime','integrations','devices','updates')

def login(d,role='Worker',domain='jobs'):
    service=IdentityService(d.store)
    identity=service.create_user(d.credentials['principal'],role.lower(),PASSWORD,role,(domain,))
    raw,principal=service.login(role.lower(),PASSWORD)
    return service,identity,raw,principal

@pytest.mark.parametrize('page',SYSTEM)
def test_system_surface_auth_scope_and_read_only(dashboard,page):
    d=dashboard;path='/api/ui/'+page
    assert request(d,path)[0]==401
    _,_,worker,_=login(d)
    assert request(d,path,raw=worker)[0]==403
    assert request(d,path,raw=d.credentials['raw'])[0]==200
    assert request(d,path,'POST',{},d.credentials['raw'])[0]==405

@pytest.mark.parametrize('role',['Owner','Administrator','Manager','Worker'])
def test_context_scope_and_hidden_global_pages(dashboard,role):
    _,_,raw,_=login(dashboard,role)
    code,_,ctx=request(dashboard,'/api/ui/context',raw=raw)
    assert code==200 and [d['id'] for d in ctx['domains']]==['jobs']
    assert 'jobOverview' in ctx['pages'] and 'models' not in ctx['pages'] and 'notifications' not in ctx['pages']
    assert 'health' not in ctx['pages']
    assert ('evidence' in ctx['pages']) == (role in {'Owner','Administrator'})
    assert ctx['domains'][0]['permissions']['work.approve']==(role in {'Owner','Administrator'})

def test_scoped_overview_and_job_state_never_leak_farm(dashboard):
    d=dashboard;_,_,raw,_=login(d)
    from domains.runtime import DomainRuntime
    from application.composition import default_registry
    foreign=DomainRuntime(d.store,registry=default_registry()).queue('farming','record_soil_test',{'plot':'PRIVATE-FARM-MARKER','sample_date':'2026-01-01','ph':6.4,'source':'test','confirmed':True})
    d.store.queue_command('Visible Job request')
    for path in ('/api/ui/overview','/api/ui/job-state'):
        code,_,body=request(d,path,raw=raw)
        assert 'PRIVATE-FARM-MARKER' not in json.dumps(body)
        if path.endswith('overview'):assert code==403
        else:
            assert code==200
            assert foreign['command_id'] not in [c['id'] for c in body['commands']]
    assert request(d,'/api/state',raw=raw)[0]==403

@pytest.mark.parametrize('page',['evidence','ledger','runbooks'])
def test_domain_views_filter_before_serializing_and_cannot_mutate(dashboard,page):
    d=dashboard;_,_,raw,_=login(d,role='Owner')
    with d.store._connect() as con:
        if page=='evidence':
            for domain in ('jobs','farming'):con.execute('INSERT INTO shared_evidence VALUES(?,?,?)',(domain,domain+'-id',json.dumps({'truth':'DOCUMENTED','verification':'UNVERIFIED','source_ref':domain+'-private','content_ref':'NEVER_EXPOSE_CONTENT','confidence':None})))
        elif page=='ledger':
            for domain in ('jobs','farming'):con.execute('INSERT INTO decision_ledger(domain,event_key,correlation_id,record) VALUES(?,?,?,?)',(domain,'key',domain+'-private',json.dumps({'action':'read','outcome':'RECORDED','rationale':'NEVER_EXPOSE_PAYLOAD'})))
        else:
            for domain in ('jobs','farming'):
                con.execute('INSERT INTO sop_definitions VALUES(?,?,?,?,?,?)',(domain,'fixture','1.0.0','digest','{}','2026-01-01T00:00:00Z'))
                con.execute('INSERT INTO sop_runs VALUES(?,?,?,?,?,?,?,?,?)',(domain,domain+'-private','fixture','1.0.0','digest','{"private":"NEVER_EXPOSE_PAYLOAD"}','REVIEW','first',1))
        before=con.total_changes
    code,_,body=request(d,'/api/ui/'+page+'/jobs',raw=raw)
    assert code==200 and len(body['rows'])==1
    text=json.dumps(body);assert 'farming-private' not in text and 'NEVER_EXPOSE' not in text
    assert request(d,'/api/ui/'+page+'/farming',raw=raw)[0]==403
    assert request(d,'/api/ui/'+page+'/jobs','POST',{},raw)[0]==405

def test_evidence_truth_verification_conflicts_and_fact_lifecycle(dashboard):
    d=dashboard
    with d.store._connect() as con:
        for identity in ('left','right'):con.execute('INSERT INTO shared_evidence VALUES(?,?,?)',('jobs',identity,json.dumps({'truth':'DOCUMENTED','verification':'UNVERIFIED','confidence':.4})))
        con.execute("INSERT INTO evidence_verifications(domain,evidence_id,state,basis,actor,received_at) VALUES('jobs','left','DISPUTED','review','test','2026-01-01T00:00:00Z')")
        con.execute("INSERT INTO evidence_contradictions(domain,conflict_id,left_id,right_id,state,reason,received_at) VALUES('jobs','conflict','left','right','OPEN','test','2026-01-01T00:00:00Z')")
    fact=d.store.add_candidate_fact({'text':'Synthetic confirmed claim','status':'USER_CONFIRMED'})
    body=request(d,'/api/ui/evidence/jobs',raw=d.credentials['raw'])[2]
    row=next(r for r in body['rows'] if r['id']=='left')
    assert row['truth']=='DOCUMENTED' and row['verification']=='DISPUTED' and row['contradictions'][0]['state']=='OPEN'
    assert row['last_recorded_quality'] is None
    assert d.store.candidate_facts(status='USER_CONFIRMED')[0]['id']==fact

def test_delegated_action_visibility_does_not_grant_other_approval(dashboard):
    d=dashboard;s,identity,raw,principal=login(d)
    a=d.store.add_action('One','submit_application');b=d.store.add_action('Two','submit_application')
    path='/api/ui/approval-access'
    assert request(d,path,raw=raw)[2]=={str(a):False,str(b):False}
    grant=s.grant(d.credentials['principal'],identity,'jobs',resource='action:'+str(a))
    assert request(d,path,raw=raw)[2]=={str(a):True,str(b):False}
    assert request(d,'/api/actions/'+str(b),'POST',{'status':'APPROVED'},raw)[0]==403
    s.revoke_grant(d.credentials['principal'],grant)
    assert request(d,path,raw=raw)[2][str(a)] is False

def test_foundation_views_are_honest_and_model_mutation_keeps_reauth(dashboard):
    d=dashboard;raw=d.credentials['raw']
    runtime=request(d,'/api/ui/runtime',raw=raw)[2]
    assert runtime['selected'] is None and runtime['decision']=='NO_QUALIFIED_CANDIDATE_YET'
    assert all(not c['qualification']['qualified'] and len(c['qualification']['blockers'])==7 for c in runtime['candidates'])
    assert request(d,'/api/ui/updates',raw=raw)[2]['activation_available'] is False
    assert request(d,'/api/ui/devices',raw=raw)[2]['status']=='UNAVAILABLE'
    assert 'OPEN' in request(d,'/api/ui/models',raw=raw)[2]['limitation']
    with d.store._connect() as con:con.execute('UPDATE human_sessions SET reauth_at=?',(utc_text(utc_now()-timedelta(minutes=6)),))
    assert request(d,'/api/ui/models',raw=raw)[0]==200
    assert request(d,'/api/model-controls','POST',{'model':'jobs.qwen','state':'DISABLED'},raw)[0]==428

def test_current_health_not_inferred_from_desired_mode(dashboard):
    raw=dashboard.credentials['raw'];rows=request(dashboard,'/api/system-health',raw=raw)[2]
    assert any(row['status']=='UNKNOWN' and row['desired_mode']=='ENABLED' for row in rows)
    assert all(row['status'] in {'UNKNOWN','HEALTHY','DEGRADED','UNAVAILABLE'} for row in rows)

def test_read_views_do_not_add_schema_or_authoritative_rows(dashboard):
    d=dashboard;excluded={'human_sessions','human_security_events'}
    def snapshot():
        with d.store._connect() as con:
            schema=[tuple(r) for r in con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name')]
            data={r[0]:[tuple(x) for x in con.execute('SELECT * FROM "'+r[0]+'"')] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'") if r[0] not in excluded}
        return schema,data
    before=snapshot()
    for page in SYSTEM:assert request(d,'/api/ui/'+page,raw=d.credentials['raw'])[0]==200
    for page in ('evidence','ledger','runbooks'):assert request(d,'/api/ui/'+page+'/jobs',raw=d.credentials['raw'])[0]==200
    assert before==snapshot()


def test_job_notification_count_requires_global_audit_permission(dashboard):
    d=dashboard
    d.store.add_notification('Private system notice','Synthetic system-only detail')
    d.store.add_notification('Private Farm notice','Synthetic Farm-only detail',domain='farming')
    _,_,worker,_=login(d)
    assert request(d,'/api/ui/job-state',raw=worker)[2]['counts']['notifications']==0
    assert request(d,'/api/ui/job-state',raw=d.credentials['raw'])[2]['counts']['notifications']==2
    assert request(d,'/api/state',raw=worker)[0]==403
