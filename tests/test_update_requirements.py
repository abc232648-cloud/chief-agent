from dataclasses import replace
import pytest
from application.composition import default_catalogs
from update_center.planner import plan_update, evaluate_evidence
from update_center.contracts import UpdateReadiness
from test_update_center import current, candidate, passing_evidence, B, C


def test_declared_models_and_benchmarks_need_independent_passes():
    plan=plan_update(current(),candidate(model_requirements=['qwen-free'],benchmark_requirements=['latency-local']),default_catalogs().capabilities)
    evidence=passing_evidence(plan)
    assert evaluate_evidence(plan,evidence) is UpdateReadiness.REJECTED
    evidence=replace(evidence,model_outcomes=(('qwen-free','PASSED'),))
    assert evaluate_evidence(plan,evidence) is UpdateReadiness.REJECTED
    evidence=replace(evidence,benchmark_outcomes=(('latency-local','PASSED'),))
    assert evaluate_evidence(plan,evidence) is UpdateReadiness.READY_FOR_MANUAL_REVIEW
    assert plan.activation=='MANUAL_ONLY_NOT_IMPLEMENTED'


@pytest.mark.parametrize('kind',['model','benchmark'])
@pytest.mark.parametrize('status',['FAILED','SKIPPED','UNKNOWN','DOCUMENTED'])
def test_nonpasses_never_satisfy_requirements(kind,status):
    plan=plan_update(current(),candidate(**{kind+'_requirements':['required']}),default_catalogs().capabilities)
    evidence=passing_evidence(plan,**{kind+'_outcomes':(('required',status),)})
    assert evaluate_evidence(plan,evidence) is UpdateReadiness.REJECTED


@pytest.mark.parametrize('kind',['model','benchmark'])
def test_duplicate_requirement_evidence_rejected(kind):
    plan=plan_update(current(),candidate(**{kind+'_requirements':['required']}),default_catalogs().capabilities)
    evidence=passing_evidence(plan,**{kind+'_outcomes':(('required','FAILED'),('required','PASSED'))})
    assert evaluate_evidence(plan,evidence) is UpdateReadiness.REJECTED


@pytest.mark.parametrize('digest',['z'*64,'a'*63,None,1])
def test_backup_reference_is_a_digest_not_just_a_length(digest):
    plan=plan_update(current(),candidate(schema_sha256=C,migrations=[{'migration_id':'fixture','from_schema':B,'to_schema':C,'requires_backup':True,'reversible':False}]),default_catalogs().capabilities)
    assert evaluate_evidence(plan,passing_evidence(plan,backup_manifest_sha256=digest)) is UpdateReadiness.REJECTED


@pytest.mark.parametrize('fresh',['true',1,[],None])
def test_freshness_requires_boolean_true(fresh):
    plan=plan_update(current(),candidate(),default_catalogs().capabilities)
    assert evaluate_evidence(plan,passing_evidence(plan,fresh=fresh)) is UpdateReadiness.REJECTED
