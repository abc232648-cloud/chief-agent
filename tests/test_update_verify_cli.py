import base64
from dataclasses import asdict
import json
from types import SimpleNamespace

import pytest

from application import update_verify
from tests.test_update_release_trust import NOW, release
from update_center import release_trust
from tests.test_update_history import store
from tests.test_update_center import current


@pytest.fixture
def inputs(tmp_path, release, monkeypatch, store):
    key, payload, sign, data = release
    monkeypatch.setattr(release_trust, 'utc_now', lambda: NOW)
    monkeypatch.setattr('deployment.launch.configure', lambda _: SimpleNamespace(database=store.path))
    policy = dict(version=1, repository=release_trust.REPOSITORY, minimum_sequence=1,
                  keys=[{**asdict(key), 'public_key': base64.b64encode(key.public_key).decode()}])
    files = {name: tmp_path / name for name in ('policy.json', 'release.json', 'source.zip', 'current.json')}
    current_value = asdict(current())
    current_value['capability_versions'] = [dict(id=k, version=v) for k, v in current_value['capability_versions']]
    files['current.json'].write_text(json.dumps(current_value), encoding='utf-8')
    files['policy.json'].write_text(json.dumps(policy), encoding='utf-8')
    files['release.json'].write_bytes(sign(payload))
    files['source.zip'].write_bytes(data)
    args = ['--envelope', str(files['release.json']), '--trust-policy', str(files['policy.json'])]
    stage = tmp_path / 'stage'
    stage.mkdir()
    return files, args, stage, policy


@pytest.mark.parametrize('mode', ['metadata', 'artifact', 'stage'])
def test_operator_verification_never_installs_or_authorizes(inputs, capsys, mode):
    files, args, stage, _ = inputs
    if mode != 'metadata':
        args += ['--archive', str(files['source.zip'])]
    if mode == 'stage':
        args += ['--staging-root', str(stage), '--confirm-private-storage', '--config', 'synthetic', '--current-manifest', str(files['current.json'])]
    assert update_verify.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == {'metadata': 'PUBLISHER_VERIFIED', 'artifact': 'ARTIFACT_VERIFIED',
                                'stage': 'STAGED_NOT_INSTALLED'}[mode]
    assert result['installed'] is False
    assert result['activation'] == 'NOT_AUTHORIZED'
    assert result['compatibility'] == ('ACCEPTANCE_REQUIRED' if mode == 'stage' else 'NOT_EVALUATED')
    if mode == 'stage':
        slot = stage / result['stage_id']
        assert {p.name for p in slot.iterdir()} == {'source.zip', 'receipt.json'}
        assert (slot / 'source.zip').read_bytes() == files['source.zip'].read_bytes()
    else:
        assert not list(stage.iterdir())


@pytest.mark.parametrize('failure', ['signature', 'archive', 'revoked', 'floor', 'unknown-field',
                                     'missing-confirmation', 'missing-archive', 'bad-key', 'duplicate-key',
                                     'relative-path', 'missing-file', 'missing-config', 'missing-current', 'incompatible'])
def test_rejection_is_inert_and_does_not_disclose_operator_paths(inputs, capsys, failure):
    files, args, stage, policy = inputs
    args += ['--archive', str(files['source.zip']), '--staging-root', str(stage), '--confirm-private-storage', '--config', 'synthetic', '--current-manifest', str(files['current.json'])]
    if failure == 'signature':
        envelope = json.loads(files['release.json'].read_bytes())
        envelope['signature'] = base64.b64encode(b'x' * 64).decode()
        files['release.json'].write_text(json.dumps(envelope), encoding='utf-8')
    elif failure == 'archive':
        files['source.zip'].write_bytes(b'private-synthetic-sentinel')
    elif failure == 'revoked':
        policy['keys'][0]['revoked'] = True
    elif failure == 'floor':
        policy['minimum_sequence'] = 3
    elif failure == 'unknown-field':
        policy['secret'] = 'private-synthetic-sentinel'
    elif failure == 'missing-confirmation':
        args.remove('--confirm-private-storage')
    elif failure == 'missing-archive':
        i = args.index('--archive')
        del args[i:i+2]
    elif failure == 'bad-key':
        policy['keys'][0]['public_key'] = 'invalid'
    elif failure == 'duplicate-key':
        policy['keys'] *= 2
    elif failure == 'relative-path':
        args[1] = 'release.json'
    elif failure == 'missing-file':
        files['release.json'].unlink()
    elif failure == 'missing-config':
        i = args.index('--config')
        del args[i:i+2]
    elif failure == 'missing-current':
        i = args.index('--current-manifest')
        del args[i:i+2]
    elif failure == 'incompatible':
        current_value = json.loads(files['current.json'].read_bytes())
        current_value['chief_version'] = '99.0.0'
        files['current.json'].write_text(json.dumps(current_value), encoding='utf-8')
    files['policy.json'].write_text(json.dumps(policy), encoding='utf-8')
    assert update_verify.main(args) == 2
    output = capsys.readouterr().out
    assert json.loads(output)['status'] == 'RELEASE_REJECTED'
    assert 'private-synthetic-sentinel' not in output and str(stage.parent) not in output
    assert not list(stage.iterdir())


@pytest.mark.parametrize('value', [True, -1, '1'])
def test_policy_floor_must_be_nonnegative_integer(inputs, value):
    files, _, _, policy = inputs
    policy['minimum_sequence'] = value
    files['policy.json'].write_text(json.dumps(policy), encoding='utf-8')
    with pytest.raises(ValueError):
        update_verify.load_policy(files['policy.json'])


def test_bounded_input_and_duplicate_policy_fields_rejected(inputs):
    files, _, _, _ = inputs
    with pytest.raises(ValueError):
        update_verify.read_bounded(files['source.zip'], 1)
    files['policy.json'].write_text('{"version":1,"version":1}', encoding='utf-8')
    with pytest.raises(ValueError):
        update_verify.load_policy(files['policy.json'])
