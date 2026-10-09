from dataclasses import replace

import pytest

from agents.manifest import CompatibilityDeclaration
from agents.module_compatibility import (
    CURRENT_CHIEF_COMPATIBILITY,
    ChiefCompatibilityProfile,
    ModuleCompatibilityEvaluator,
    evaluate_manifest,
)
from application.composition import default_catalogs
from domains.jobs.manifest import manifest as jobs_manifest


def with_requires(**changes):
    manifest = jobs_manifest()
    requires = replace(manifest.requires, **changes)
    return replace(manifest, requires=requires)


def issue_codes(result):
    return {item.code for item in result.issues}


def test_current_jobs_and_farming_manifests_are_compatible():
    manifests = default_catalogs().manifests
    evaluator = ModuleCompatibilityEvaluator(manifests)
    results = {item['module_id']: item for item in evaluator.describe()}
    assert results['jobs']['compatible'] is True
    assert results['farming']['compatible'] is True
    assert results['jobs']['issues'] == []
    assert results['jobs']['authority'] == 'CHIEF_DERIVED'


def test_chief_range_accepts_current_version_and_rejects_upper_boundary():
    assert evaluate_manifest(jobs_manifest()).compatible is True
    profile = ChiefCompatibilityProfile('2.0.0', '1', '1', '1')
    result = evaluate_manifest(jobs_manifest(), profile)
    assert result.compatible is False
    assert issue_codes(result) == {'CHIEF_VERSION_MISMATCH'}
    assert '>=1.0.0 <2.0.0' in result.reason


@pytest.mark.parametrize('requirement', ['^1.0.0', '>=1.0', '1.x', '>=1.0.0 || <2.0.0'])
def test_unsupported_chief_requirement_syntax_fails_closed(requirement):
    result = evaluate_manifest(with_requires(chief=requirement))
    assert result.compatible is False
    assert 'CHIEF_REQUIREMENT_INVALID' in issue_codes(result)


def test_interface_api_mismatch_fails_closed():
    result = evaluate_manifest(with_requires(owner_api='2'))
    assert result.compatible is False
    assert issue_codes(result) == {'INTERFACE_API_MISMATCH'}
    assert 'owner_api 2' in result.reason


def test_declared_interface_requires_api_compatibility_level():
    result = evaluate_manifest(with_requires(owner_api=None))
    assert result.compatible is False
    assert issue_codes(result) == {'INTERFACE_REQUIREMENT_MISSING'}


def test_api_requirement_without_interface_fails_closed():
    result = evaluate_manifest(with_requires(staff_api='1'))
    assert result.compatible is False
    assert issue_codes(result) == {'INTERFACE_REQUIREMENT_ORPHANED'}


def test_evaluation_is_deterministic_and_does_not_mutate_registry():
    manifests = default_catalogs().manifests
    evaluator = ModuleCompatibilityEvaluator(manifests)
    before = manifests.describe()
    first = evaluator.evaluate('jobs')
    second = evaluator.evaluate('jobs')
    after = manifests.describe()
    assert first == second
    assert before == after


def test_unknown_manifest_is_rejected_by_authoritative_registry():
    evaluator = ModuleCompatibilityEvaluator(default_catalogs().manifests)
    with pytest.raises(ValueError, match='Unknown agent manifest'):
        evaluator.evaluate('security')


def test_profile_contract_is_strict_and_bounded_to_supported_version_shapes():
    assert CURRENT_CHIEF_COMPATIBILITY.chief_version == '1.0.0'
    with pytest.raises(ValueError, match='major.minor.patch'):
        ChiefCompatibilityProfile('1.0', '1', '1', '1')
    with pytest.raises(ValueError, match='Owner Api'):
        ChiefCompatibilityProfile('1.0.0', 'v1', '1', '1')
