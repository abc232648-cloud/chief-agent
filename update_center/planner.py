"""Dependency-aware update planning without installation or activation."""
from dataclasses import asdict
import hashlib
import json

from capabilities.contracts import Node, version
from capabilities.regression import regression_plan
from compatibility.contracts import ReleaseManifest
from compatibility.manifest import manifest_digest
from .contracts import UpdateEvidence, UpdatePlan, UpdateReadiness


def _nodes(keys):
    result = []
    for key in keys:
        kind, sep, identity = key.partition(':')
        if not sep:
            raise ValueError('Changed node must use kind:id.')
        result.append(Node(kind, identity))
    return tuple(result)


def _capability_versions(manifest):
    return dict(manifest.capability_versions)


def plan_update(current, candidate, registry):
    if not isinstance(current, ReleaseManifest) or not isinstance(candidate, ReleaseManifest):
        raise ValueError('Current and candidate manifests are required.')
    blockers = []
    if version(candidate.chief_version) < version(current.chief_version):
        blockers.append('ARBITRARY_DOWNGRADE_NOT_SUPPORTED')
    if not candidate.private_state_excluded:
        blockers.append('PRIVATE_STATE_NOT_EXCLUDED')
    if current.release_id not in candidate.rollback_compatible_with:
        blockers.append('CURRENT_RELEASE_NOT_DECLARED_ROLLBACK_COMPATIBLE')
    declared = _capability_versions(candidate)
    known = registry.capabilities
    for identity, declared_version in declared.items():
        if identity in known and declared_version != known[identity].version:
            # Candidate manifests may describe a future contract, but the local planner cannot
            # certify it until the candidate catalog is loaded and tested.
            blockers.append('CAPABILITY_VERSION_REQUIRES_CANDIDATE_CATALOG:' + identity)
    for runtime in candidate.runtime_requirements:
        if runtime.required:
            blockers.append('RUNTIME_QUALIFICATION_REQUIRED:' + runtime.runtime)
    schema_changed = current.schema_sha256 != candidate.schema_sha256
    migrations = tuple(m.migration_id for m in candidate.migrations)
    if schema_changed:
        matching = [m for m in candidate.migrations if m.from_schema == current.schema_sha256 and m.to_schema == candidate.schema_sha256]
        if len(matching) != 1:
            blockers.append('SCHEMA_CHANGE_WITHOUT_EXACT_MIGRATION')
    else:
        if candidate.migrations:
            blockers.append('MIGRATION_DECLARED_WITHOUT_SCHEMA_CHANGE')
    changes = _nodes(candidate.changed_nodes)
    if not changes:
        raise ValueError('Candidate must declare changed components/capabilities.')
    regression = regression_plan(registry, changes, candidate=candidate.source_tree_sha256)
    required = set(candidate.required_tests) | set(regression.required_tests)
    optional = set(regression.optional_tests) - required
    backup_required = schema_changed or any(m.requires_backup for m in candidate.migrations)
    payload = {
        'current': manifest_digest(current), 'candidate': manifest_digest(candidate),
        'regression': regression.id, 'required': sorted(required), 'optional': sorted(optional),
        'blockers': sorted(set(blockers)), 'backup_required': backup_required,
    }
    plan_id = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return UpdatePlan(plan_id, current.release_id, candidate.release_id, candidate.source_tree_sha256,
                      tuple(sorted(candidate.changed_nodes)), tuple(sorted(required)), tuple(sorted(optional)),
                      regression.required_consumers, backup_required, tuple(sorted(migrations)),
                      tuple(sorted(set(blockers))))


def evaluate_evidence(plan, evidence):
    """Return readiness for manual operator review; never activates an update."""
    if not isinstance(plan, UpdatePlan) or not isinstance(evidence, UpdateEvidence):
        raise ValueError('Plan and evidence are required.')
    if evidence.plan_id != plan.plan_id or evidence.candidate_source != plan.candidate_source:
        return UpdateReadiness.REJECTED
    if plan.blockers or not evidence.fresh or evidence.health_status != 'HEALTHY':
        return UpdateReadiness.REJECTED
    outcomes = dict(evidence.test_outcomes)
    if len(outcomes) != len(evidence.test_outcomes):
        return UpdateReadiness.REJECTED
    if any(outcomes.get(name) != 'PASSED' for name in plan.required_tests):
        return UpdateReadiness.REJECTED
    if plan.backup_required and (not evidence.backup_manifest_sha256 or len(evidence.backup_manifest_sha256) != 64):
        return UpdateReadiness.REJECTED
    if not evidence.human_approval_ref:
        return UpdateReadiness.STAGING_REQUIRED
    return UpdateReadiness.READY_FOR_MANUAL_REVIEW
