import json

import pytest

from deployment.runtime_profiles import RuntimeProfileConflict,inspect_profile,prepare_profile


def test_missing_runtime_profile_is_reported_without_creation(tmp_path):
    plan=inspect_profile(tmp_path,'runtime-a-1.0')
    assert plan['status']=='CREATE_REQUIRED'
    assert not (tmp_path/'runtime-profiles').exists()


def test_prepare_profile_creates_private_owned_profile_without_touching_other_state(tmp_path):
    existing=tmp_path/'existing-user-profile';existing.mkdir();(existing/'keep.txt').write_text('untouched')
    result=prepare_profile(tmp_path,'runtime-a-1.0')
    profile=tmp_path/'runtime-profiles/runtime-a-1.0'
    assert result['status']=='CHIEF_OWNED' and profile.is_dir()
    assert json.loads((profile/'.chief-runtime-profile.json').read_text())=={
        'format_version':1,'owner':'CHIEF','candidate_id':'runtime-a-1.0'}
    assert (existing/'keep.txt').read_text()=='untouched'


def test_unmarked_existing_reserved_profile_fails_instead_of_being_adopted(tmp_path):
    profile=tmp_path/'runtime-profiles/runtime-a-1.0';profile.mkdir(parents=True)
    (profile/'foreign-state').write_text('do not import')
    with pytest.raises(RuntimeProfileConflict,match='not Chief-owned'):
        inspect_profile(tmp_path,'runtime-a-1.0')
    assert (profile/'foreign-state').read_text()=='do not import'


def test_profile_marker_cannot_be_reused_for_another_candidate(tmp_path):
    prepare_profile(tmp_path,'runtime-a-1.0')
    target=tmp_path/'runtime-profiles/runtime-b-2.0';target.mkdir()
    (target/'.chief-runtime-profile.json').write_text(json.dumps({
        'format_version':1,'owner':'CHIEF','candidate_id':'runtime-a-1.0'}))
    with pytest.raises(RuntimeProfileConflict,match='another runtime identity'):
        inspect_profile(tmp_path,'runtime-b-2.0')


@pytest.mark.parametrize('candidate',['','../escape','A Runtime','a/b','a\\b'])
def test_runtime_candidate_id_cannot_escape_private_profile_root(tmp_path,candidate):
    with pytest.raises(ValueError):inspect_profile(tmp_path,candidate)
