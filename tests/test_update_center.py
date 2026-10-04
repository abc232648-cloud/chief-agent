from dataclasses import replace

from application.composition import default_catalogs
from compatibility.manifest import load_manifest
from update_center.contracts import UpdateEvidence, UpdateReadiness
from update_center.planner import plan_update, evaluate_evidence

A='a'*64;B='b'*64;C='c'*64


def manifest(release,version,source,schema,**kw):
    p={
      'format_version':1,'release_id':release,'chief_version':version,'source_tree_sha256':source,'schema_sha256':schema,
      'capability_versions':[{'id':'chief.domain_dispatch','version':'1.0.0'}],
      'changed_nodes':['capability:chief.domain_dispatch'],'required_tests':['tests/test_domains.py'],
      'runtime_requirements':[],'model_requirements':[],'migrations':[],
      'rollback_compatible_with':['chief-1.0.0'],'benchmark_requirements':[],
      'private_state_excluded':True,'created_at':'2026-09-24T10:00:00+00:00'}
    p.update(kw);return load_manifest(p)


def current():return manifest('chief-1.0.0','1.0.0',A,B,rollback_compatible_with=[])

def candidate(**kw):return manifest('chief-1.0.1','1.0.1',C,B,**kw)


def passing_evidence(plan,**kw):
    value=dict(plan_id=plan.plan_id,candidate_source=plan.candidate_source,
               test_outcomes=tuple((x,'PASSED') for x in plan.required_tests),
               backup_manifest_sha256=None,human_approval_ref='approval:fixture',health_status='HEALTHY',fresh=True)
    value.update(kw);return UpdateEvidence(**value)


def test_plan_unions_declared_and_dependency_generated_tests():
    plan=plan_update(current(),candidate(),default_catalogs().capabilities)
    assert not plan.blockers
    assert 'tests/test_domains.py' in plan.required_tests
    assert 'tests/test_command_processor.py' in plan.required_tests
    assert plan.activation=='MANUAL_ONLY_NOT_IMPLEMENTED'
    assert plan.backup_required is False


def test_arbitrary_downgrade_is_blocked():
    cand=manifest('chief-0.9.0','0.9.0',C,B,rollback_compatible_with=['chief-1.0.0'])
    plan=plan_update(current(),cand,default_catalogs().capabilities)
    assert 'ARBITRARY_DOWNGRADE_NOT_SUPPORTED' in plan.blockers


def test_private_state_in_release_is_blocked():
    plan=plan_update(current(),candidate(private_state_excluded=False),default_catalogs().capabilities)
    assert 'PRIVATE_STATE_NOT_EXCLUDED' in plan.blockers


def test_required_runtime_blocks_until_qualification_is_separate():
    cand=candidate(runtime_requirements=[{'runtime':'openclaw','minimum':'1.0.0','before':'2.0.0','required':True}])
    plan=plan_update(current(),cand,default_catalogs().capabilities)
    assert 'RUNTIME_QUALIFICATION_REQUIRED:openclaw' in plan.blockers


def test_schema_change_requires_exact_migration_and_backup():
    bad=candidate(schema_sha256=C)
    plan=plan_update(current(),bad,default_catalogs().capabilities)
    assert 'SCHEMA_CHANGE_WITHOUT_EXACT_MIGRATION' in plan.blockers
    good=candidate(schema_sha256=C,migrations=[{'migration_id':'g-1','from_schema':B,'to_schema':C,'requires_backup':True,'reversible':False}])
    plan=plan_update(current(),good,default_catalogs().capabilities)
    assert 'SCHEMA_CHANGE_WITHOUT_EXACT_MIGRATION' not in plan.blockers
    assert plan.backup_required is True


def test_evidence_must_be_exact_fresh_healthy_complete_and_human_reviewed():
    plan=plan_update(current(),candidate(),default_catalogs().capabilities)
    assert evaluate_evidence(plan,passing_evidence(plan)) is UpdateReadiness.READY_FOR_MANUAL_REVIEW
    assert evaluate_evidence(plan,passing_evidence(plan,fresh=False)) is UpdateReadiness.REJECTED
    assert evaluate_evidence(plan,passing_evidence(plan,health_status='DEGRADED')) is UpdateReadiness.REJECTED
    assert evaluate_evidence(plan,passing_evidence(plan,human_approval_ref=None)) is UpdateReadiness.STAGING_REQUIRED
    tests=list(passing_evidence(plan).test_outcomes);tests[0]=(tests[0][0],'SKIPPED')
    assert evaluate_evidence(plan,passing_evidence(plan,test_outcomes=tuple(tests))) is UpdateReadiness.REJECTED
    assert evaluate_evidence(plan,passing_evidence(plan,candidate_source='f'*64)) is UpdateReadiness.REJECTED


def test_blocked_plan_cannot_be_overridden_by_green_tests():
    plan=plan_update(current(),candidate(private_state_excluded=False),default_catalogs().capabilities)
    assert evaluate_evidence(plan,passing_evidence(plan)) is UpdateReadiness.REJECTED

def test_cli_outputs_plan_and_never_activates(tmp_path,capsys):
    from application.update_plan import main
    import json
    cur=current();cand=candidate()
    def encode(m):
        from dataclasses import asdict
        raw=asdict(m)
        raw['capability_versions']=[{'id':x[0],'version':x[1]} for x in m.capability_versions]
        raw['runtime_requirements']=[asdict(x) for x in m.runtime_requirements]
        raw['migrations']=[asdict(x) for x in m.migrations]
        return raw
    a=tmp_path/'current.json';b=tmp_path/'candidate.json'
    a.write_text(json.dumps(encode(cur)));b.write_text(json.dumps(encode(cand)))
    assert main([str(a),str(b)])==0
    payload=json.loads(capsys.readouterr().out)
    assert payload['activation']=='MANUAL_ONLY_NOT_IMPLEMENTED'
    assert not payload['blockers']
