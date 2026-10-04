from dataclasses import dataclass
from enum import Enum


class UpdateReadiness(str, Enum):
    REJECTED = 'REJECTED'
    STAGING_REQUIRED = 'STAGING_REQUIRED'
    READY_FOR_MANUAL_REVIEW = 'READY_FOR_MANUAL_REVIEW'


@dataclass(frozen=True)
class UpdatePlan:
    plan_id: str
    current_release: str
    candidate_release: str
    candidate_source: str
    changed_nodes: tuple[str, ...]
    required_tests: tuple[str, ...]
    optional_tests: tuple[str, ...]
    required_consumers: tuple[str, ...]
    backup_required: bool
    migration_ids: tuple[str, ...]
    blockers: tuple[str, ...]
    activation: str = 'MANUAL_ONLY_NOT_IMPLEMENTED'


@dataclass(frozen=True)
class UpdateEvidence:
    plan_id: str
    candidate_source: str
    test_outcomes: tuple[tuple[str, str], ...]
    backup_manifest_sha256: str | None
    human_approval_ref: str | None
    health_status: str
    fresh: bool
