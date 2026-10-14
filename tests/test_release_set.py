import json
from pathlib import Path
import pytest

from deployment.release_set import load_release_set, verify_built_clients


def release_path():
    return Path(__file__).resolve().parents[1] / 'release' / 'release-set-2026-10-14.json'


def test_committed_release_set_is_strict_and_complete():
    value = load_release_set(release_path())
    assert value['release_set'] == 'chief-install-closure-2026-10-14'
    assert value['backend_contract'] == 'chief-install-closure-v1'
    assert {x['app'] for x in value['clients']} == {'owner', 'staff', 'jobs'}
    assert {x['app'] for x in value['clients'] if x['required']} == {'owner', 'staff'}


def test_built_clients_must_match_release_contract(tmp_path):
    value = load_release_set(release_path())
    for item in value['clients']:
        if item['required']:
            folder = tmp_path / item['app']; folder.mkdir()
            (folder / 'release.json').write_text(json.dumps({
                'format_version': 1, 'app': item['app'], 'release_set': value['release_set'],
                'backend_contract': value['backend_contract'], 'route': item['route']}), encoding='utf-8')
    assert verify_built_clients(value, tmp_path) == {'owner':'VERIFIED','staff':'VERIFIED','jobs':'OPTIONAL_NOT_PRESENT'}
    marker = tmp_path / 'staff' / 'release.json'
    wrong = json.loads(marker.read_text()); wrong['backend_contract'] = 'wrong'; marker.write_text(json.dumps(wrong))
    with pytest.raises(ValueError): verify_built_clients(value, tmp_path)


def test_release_set_rejects_unreviewed_shape(tmp_path):
    value = json.loads(release_path().read_text())
    value['unexpected'] = True
    path = tmp_path / 'release.json'; path.write_text(json.dumps(value))
    with pytest.raises(ValueError): load_release_set(path)
