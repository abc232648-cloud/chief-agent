from dataclasses import replace
import json

import pytest

from database.store import Store
from identity.service import IdentityService
from policy.contracts import Outcome, PolicyPack, PolicyRule, Precedence
from policy.engine import PolicyEngine
from tests.checkpoint_f_fixture import PASSWORD, secure_store
from tests.test_managed_updates import managed
from tests.test_update_release_trust import NOW, release
from update_center.acceptance import PREFIX, run_candidate as _run_candidate
from update_center.contracts import UpdatePlan


def run_candidate(*args, **kwargs):
    return _run_candidate(*args, now=NOW, **kwargs)


@pytest.fixture
def acceptance(managed, tmp_path):
    installation, previous, candidate = managed
    data, envelope, keys, target = candidate({'tests/test_synthetic.py': b'def test_record():\n    assert 2+2==4\n'})
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    store = Store(tmp_path / 'authority.db')
    _, owner = secure_store(store)
    plan = UpdatePlan('a'*64, 'baseline', 'candidate', target, ('component:chief',),
                      ('tests/test_synthetic.py',), (), (), False, (), ())
    policy = PolicyEngine(())  # ASK still requires the explicit reauthenticated human.
    return installation, plan, store, owner, policy, keys


def test_acceptance_executes_real_tests_without_inheriting_keys(acceptance, tmp_path, monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-never-inherit')
    result = run_candidate(*acceptance, evidence_parent=tmp_path, confirm_reviewed_code=True)
    assert result['status'] == 'PASSED'
    assert result['counts'] == dict(passed=1, failed=0, skipped=0)
    assert result['activation_authorized'] is False
    installation, plan, store, owner, _, keys = acceptance
    assert installation.selection() == result['current_source']
    with store._connect() as con:
        saved = con.execute('SELECT value FROM control_state WHERE key=?', (PREFIX+result['run_id'],)).fetchone()[0]
        assert json.loads(saved) == result and 'synthetic-never-inherit' not in saved
        assert con.execute("SELECT COUNT(*) FROM human_security_events WHERE operation='UPDATE_TESTS_FINISHED'").fetchone()[0] == 1


@pytest.mark.parametrize('role', ['Manager', 'Worker'])
def test_staff_cannot_execute_candidate_code(acceptance, tmp_path, role):
    installation, plan, store, owner, policy, keys = acceptance
    identity = IdentityService(store)
    identity.create_user(owner, 'synthetic-user', PASSWORD, role, ('farming',))
    _, staff = identity.login('synthetic-user', PASSWORD)
    with pytest.raises(PermissionError):
        run_candidate(installation, plan, store, staff, policy, keys, evidence_parent=tmp_path, confirm_reviewed_code=True)
    assert not list(tmp_path.glob('update-tests-*'))


def test_unapproved_or_policy_blocked_code_never_runs(acceptance, tmp_path):
    with pytest.raises(PermissionError):
        run_candidate(*acceptance, evidence_parent=tmp_path)
    block = PolicyEngine((PolicyPack('security', '1.0.0', Precedence.HARD_SAFETY_SECURITY,
                                    (PolicyRule('*', Outcome.BLOCK, 'Fixture prohibition', True),)),))
    with pytest.raises(PermissionError):
        run_candidate(*acceptance[:4], block, acceptance[5], evidence_parent=tmp_path, confirm_reviewed_code=True)
    assert not list(tmp_path.glob('update-tests-*'))


@pytest.mark.parametrize('field,value', [('required_models', ('model:unqualified',)),
    ('required_benchmarks', ('benchmark:missing',)), ('migration_ids', ('migration',)),
    ('required_tests', ('../outside.py',)), ('required_tests', ())])
def test_unresolved_or_unsafe_requirements_do_not_get_pass_receipt(acceptance, tmp_path, field, value):
    installation, plan, store, owner, policy, keys = acceptance
    with pytest.raises(ValueError):
        run_candidate(installation, replace(plan, **{field:value}), store, owner, policy, keys,
                      evidence_parent=tmp_path, confirm_reviewed_code=True)
    assert not list(tmp_path.glob('update-tests-*'))


@pytest.mark.parametrize('body,expected', [
    (b'import pytest\ndef test_record():\n    pytest.skip("not qualified")\n', 'FAILED'),
    (b'def test_record():\n    assert False\n', 'FAILED'),
    (b'import os\ndef test_record():\n    assert "GROQ_API_KEY" not in os.environ\n', 'PASSED')])
def test_test_failure_skip_and_secret_isolation_are_observed(managed, tmp_path, monkeypatch, body, expected):
    installation, _, candidate = managed
    data, envelope, keys, target = candidate({'tests/test_synthetic.py': body})
    installation.stage(data, envelope, keys, minimum_sequence=1, now=NOW)
    store = Store(tmp_path / 'authority.db'); _, owner = secure_store(store)
    plan = UpdatePlan('b'*64, 'baseline', 'candidate', target, ('component:chief',),
                      ('tests/test_synthetic.py',), (), (), False, (), ())
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-secret')
    result = run_candidate(installation, plan, store, owner, PolicyEngine(()), keys,
                          evidence_parent=tmp_path, confirm_reviewed_code=True)
    assert result['status'] == expected and result['activation_authorized'] is False


def test_revoked_publisher_blocks_execution_even_after_valid_staging(acceptance, tmp_path):
    installation, plan, store, owner, policy, keys = acceptance
    with pytest.raises(ValueError):
        run_candidate(installation, plan, store, owner, policy, [replace(keys[0], revoked=True)],
                      evidence_parent=tmp_path, confirm_reviewed_code=True)
    assert not list(tmp_path.glob('update-tests-*'))
