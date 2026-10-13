from dataclasses import replace
from datetime import timedelta
import json

import pytest

from application.composition import default_catalogs
from capabilities.contracts import Mode, Node
from database.component_state import ComponentState
from identity.service import IdentityService
from policy.contracts import Outcome, PolicyPack, PolicyRule, Precedence
from policy.engine import PolicyEngine
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_managed_updates import managed
from tests.test_update_acceptance import acceptance, run_candidate
from tests.test_update_release_trust import release
from update_center.acceptance import PREFIX as EVIDENCE_PREFIX
from update_center.approval import UpdateApprovals


@pytest.fixture
def reviewed(acceptance, tmp_path):
    installation, plan, store, owner, policy, _ = acceptance
    result = run_candidate(*acceptance, evidence_parent=tmp_path, confirm_reviewed_code=True)
    approvals = UpdateApprovals(store, default_catalogs().capabilities, policy)
    return approvals, installation, plan, store, owner, result


def test_exact_review_can_be_claimed_once_without_switching_source(reviewed):
    approvals, installation, plan, store, owner, result = reviewed
    current = installation.selection()
    decision = approvals.approve(owner, plan, result['run_id'], current_source=current)
    assert decision['risk'] == 'HIGH_IMPACT'
    assert approvals.claim(owner, decision['id'], plan, current_source=current)['status'] == 'CLAIMED'
    with pytest.raises(PermissionError):
        approvals.claim(owner, decision['id'], plan, current_source=current)
    assert installation.selection() == current
    with store._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM human_security_events WHERE operation='UPDATE_REVIEW_CLAIMED'").fetchone()[0] == 1


@pytest.mark.parametrize('change', ['source', 'plan', 'evidence', 'disabled', 'policy', 'expired', 'revoked', 'environment'])
def test_review_revalidates_all_mutable_authority(reviewed, change):
    approvals, installation, plan, store, owner, result = reviewed
    current = installation.selection()
    decision = approvals.approve(owner, plan, result['run_id'], current_source=current)
    if change == 'source':
        current = 'f'*64
    elif change == 'plan':
        plan = replace(plan, plan_id='f'*64)
    elif change == 'evidence':
        with store._connect() as con:
            altered = dict(result, counts=dict(passed=1, failed=1, skipped=0))
            con.execute('UPDATE control_state SET value=? WHERE key=?', (json.dumps(altered), EVIDENCE_PREFIX+result['run_id']))
    elif change == 'disabled':
        with store._connect() as con:
            ComponentState(store).put(con, Node('component', 'chief.update-center'), Mode.DISABLED, actor='synthetic', reason='test')
    elif change == 'policy':
        approvals.policy = PolicyEngine((PolicyPack('safety', '1.0.0', Precedence.HARD_SAFETY_SECURITY,
            (PolicyRule('*', Outcome.BLOCK, 'Synthetic hard block', True),)),))
    elif change == 'expired':
        from operations.time_integrity import aware_utc
        approvals.now = lambda: aware_utc(decision['expires_at'])
    elif change == 'revoked':
        with store._connect() as con:
            con.execute('UPDATE human_sessions SET revoked=1 WHERE id=?', (owner.session_id,))
    elif change == 'environment':
        approvals.environment_identity = lambda:'f'*64
    with pytest.raises((PermissionError, ValueError)):
        approvals.claim(owner, decision['id'], plan, current_source=current)


@pytest.mark.parametrize('role', ['Manager', 'Worker'])
def test_staff_cannot_review_update_acceptance(reviewed, role):
    approvals, installation, plan, store, owner, result = reviewed
    identity = IdentityService(store)
    identity.create_user(owner, 'synthetic-staff', PASSWORD, role, ('farming',))
    _, staff = identity.login('synthetic-staff', PASSWORD)
    with pytest.raises(PermissionError):
        approvals.approve(staff, plan, result['run_id'], current_source=installation.selection())


def test_stale_and_future_evidence_cannot_be_approved(reviewed):
    approvals, installation, plan, store, owner, result = reviewed
    from operations.time_integrity import utc_now, utc_text
    for offset in (timedelta(days=-2), timedelta(minutes=1)):
        stamp = utc_text(utc_now()+offset)
        changed = dict(result, started_at=stamp, finished_at=stamp)
        with store._connect() as con:
            con.execute('UPDATE control_state SET value=? WHERE key=?', (json.dumps(changed), EVIDENCE_PREFIX+result['run_id']))
        with pytest.raises(ValueError):
            approvals.approve(owner, plan, result['run_id'], current_source=installation.selection())


def test_approval_window_uses_one_clock_read_even_on_fine_grained_clocks(reviewed):
    from itertools import count
    from operations.time_integrity import aware_utc, utc_now
    approvals, installation, plan, _, owner, result = reviewed
    base, ticks = utc_now(), count()
    approvals.now = lambda: base+timedelta(microseconds=next(ticks))
    current = installation.selection()
    decision = approvals.approve(owner, plan, result['run_id'], current_source=current)
    assert aware_utc(decision['expires_at'])-aware_utc(decision['approved_at']) == timedelta(minutes=5)
    assert approvals.claim(owner, decision['id'], plan, current_source=current)['status'] == 'CLAIMED'
