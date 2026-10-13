from dataclasses import asdict
import json

import pytest

from application.update_manage import main
from deployment.service_runtime import atomic_json
from tests.checkpoint_f_fixture import PASSWORD
from tests.test_managed_updates import managed
from tests.test_update_coordinator import coordinator
from tests.test_update_release_trust import release


def arguments(fixture, tmp_path, operation):
    updater, _, candidate, _, trust = fixture
    manifest = asdict(updater.current)
    manifest['capability_versions'] = [dict(id=k,version=v) for k,v in updater.current.capability_versions]
    baseline = tmp_path / 'current-release.json'; atomic_json(baseline, manifest)
    policy = tmp_path / 'update-policy.json'
    atomic_json(policy, dict(version=1, validation='ASK', selection='ASK', recovery='ASK'))
    return [operation, '--installation', str(updater.installation.root), '--current-manifest', str(baseline),
        '--trust-policy', str(trust), '--update-policy', str(policy), '--private-work-root', str(tmp_path),
        '--candidate', candidate, '--confirm-private-storage']


def test_cli_validates_with_private_credentials_and_revokes_its_session(coordinator, tmp_path, monkeypatch, capsys):
    answers = iter(('fixture-owner', 'VALIDATE'))
    monkeypatch.setattr('builtins.input', lambda *_:next(answers))
    monkeypatch.setattr('application.update_manage.getpass.getpass', lambda *_:PASSWORD)
    assert main(arguments(coordinator, tmp_path, 'validate')) == 0
    output = capsys.readouterr().out
    assert PASSWORD not in output
    result = json.loads(output.strip().splitlines()[-1])
    assert result['status'] == 'PASSED' and result['activation_authorized'] is False
    with coordinator[3]._connect() as con:
        assert con.execute('SELECT COUNT(*) FROM human_sessions WHERE revoked=1').fetchone()[0] == 1


def test_cli_apply_requires_explicit_confirmation_and_uses_existing_acceptance(coordinator, tmp_path, monkeypatch, capsys):
    updater, owner, candidate, _, _ = coordinator
    validated = updater.validate(owner, candidate, confirm_reviewed_code=True)
    args = arguments(coordinator, tmp_path, 'apply')+['--run-id', validated['run_id']]
    answers = iter(('fixture-owner', 'cancel'))
    monkeypatch.setattr('builtins.input', lambda *_:next(answers))
    monkeypatch.setattr('application.update_manage.getpass.getpass', lambda *_:PASSWORD)
    assert main(args) == 2
    assert updater.installation.selection() == updater.current.source_tree_sha256
    capsys.readouterr()
    answers = iter(('fixture-owner', 'APPLY'))
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result['status'] == 'SOURCE_SELECTED_RESTART_REQUIRED'
    assert result['services_started'] is False and result['data_restored'] is False


def test_cli_never_exposes_password_on_rejected_login(coordinator, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr('builtins.input', lambda *_:'fixture-owner')
    secret = 'synthetic-wrong-password-do-not-print'
    monkeypatch.setattr('application.update_manage.getpass.getpass', lambda *_:secret)
    assert main(arguments(coordinator, tmp_path, 'validate')) == 2
    output = capsys.readouterr().out
    assert secret not in output and 'UPDATE_NOT_COMPLETED' in output
