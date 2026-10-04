"""Staging-only compatibility and update planning. No activation path exists here."""
from .contracts import UpdateEvidence, UpdatePlan, UpdateReadiness
from .planner import plan_update, evaluate_evidence

__all__ = ['UpdateEvidence', 'UpdatePlan', 'UpdateReadiness', 'plan_update', 'evaluate_evidence']
