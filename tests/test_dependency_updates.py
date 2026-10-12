from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from update_center.dependency_check import (
    DependencyUpdateStateError,
    check_if_due,
    compare_versions,
    fetch_json,
    load_policy,
)

ROOT = Path(__file__).resolve().parents[1]


def _fetch(url):
    if 'openclaw/openclaw' in url:
        return {'tag_name': 'v2026.10.2'}
    if 'NousResearch/hermes-agent' in url:
        return {'name': 'Hermes Agent v0.21.4'}
    if 'pypi.org/pypi/' in url:
        return {'info': {'version': '9.0.0'}}
    raise AssertionError(url)


def _installed(_name):
    return '1.0.0'


def test_default_and_operator_selected_cadence_are_separate_from_scheduler_polling():
    policy = load_policy(ROOT, environ={})
    assert policy['cadence_hours'] == 24
    assert policy['cadence_env'] == 'DEPENDENCY_UPDATE_CHECK_HOURS'
    assert load_policy(ROOT, environ={'DEPENDENCY_UPDATE_CHECK_HOURS': '6'})['cadence_hours'] == 6
    for invalid in ('0', '-1', '721', 'daily', '1.5'):
        with pytest.raises(ValueError, match='DEPENDENCY_UPDATE_CHECK_HOURS'):
            load_policy(ROOT, environ={'DEPENDENCY_UPDATE_CHECK_HOURS': invalid})


def test_version_comparison_is_conservative():
    assert compare_versions('1.2.3', '1.2.4') == 'UPDATE_AVAILABLE'
    assert compare_versions('1.2', '1.2.0') == 'CURRENT'
    assert compare_versions('2.0.0', '1.9.9') == 'LOCAL_NEWER'
    assert compare_versions('1.0rc1', '1.0.0') == 'UNORDERED'
    assert compare_versions('1.0.0', 'latest') == 'UNORDERED'


def test_check_discovers_runtime_and_python_updates_without_installing_and_deduplicates(tmp_path):
    state_root = (tmp_path / 'state').resolve()
    now = datetime(2026, 10, 12, 7, tzinfo=timezone.utc)
    first = check_if_due(ROOT, state_root, environ={}, now=now, fetcher=_fetch, installed_version=_installed)
    assert first['status'] == 'CHECKED'
    assert first['cadence_hours'] == 24
    assert first['updates']
    assert first['new_updates'] == first['updates']
    assert {row['kind'] for row in first['updates']} == {'runtime', 'python'}

    early = check_if_due(ROOT, state_root, environ={}, now=now + timedelta(hours=23), fetcher=lambda _url: pytest.fail('not due'), installed_version=_installed)
    assert early['status'] == 'NOT_DUE'
    assert early['new_updates'] == []

    repeated = check_if_due(ROOT, state_root, environ={}, now=now + timedelta(hours=24), fetcher=_fetch, installed_version=_installed)
    assert repeated['status'] == 'CHECKED'
    assert repeated['updates']
    assert repeated['new_updates'] == []

    state = json.loads((state_root / 'maintenance/dependency-updates.json').read_text())
    assert state['notified_update_keys']
    assert all('token' not in json.dumps(row).lower() for row in state['observations'])


def test_source_failure_and_unordered_version_never_claim_current(tmp_path):
    state_root = (tmp_path / 'state').resolve()
    def fetcher(url):
        if 'openclaw/openclaw' in url:
            raise OSError('network unavailable')
        if 'NousResearch/hermes-agent' in url:
            return {'name': 'Hermes Agent v0.21.3-beta'}
        return {'info': {'version': '9.0rc1'}}
    result = check_if_due(ROOT, state_root, environ={}, fetcher=fetcher, installed_version=_installed)
    statuses = {row['status'] for row in json.loads((state_root / 'maintenance/dependency-updates.json').read_text())['observations']}
    assert result['status'] == 'CHECKED_WITH_ERRORS'
    assert result['errors'] >= 1
    assert 'SOURCE_ERROR' in statuses
    assert 'UNORDERED' in statuses
    assert 'UPDATE_AVAILABLE' not in statuses


def test_uninstalled_optional_python_dependency_is_observed_without_false_update(tmp_path):
    from importlib import metadata
    def missing(_name):
        raise metadata.PackageNotFoundError
    result = check_if_due(ROOT, (tmp_path / 'state').resolve(), environ={}, fetcher=_fetch, installed_version=missing)
    rows = json.loads(((tmp_path / 'state') / 'maintenance/dependency-updates.json').read_text())['observations']
    assert any(row['kind'] == 'python' and row['status'] == 'NOT_INSTALLED_HERE' for row in rows)
    assert all(row['kind'] != 'python' for row in result['updates'])


def test_metadata_fetch_rejects_non_https_and_unapproved_hosts_before_network():
    with pytest.raises(ValueError, match='HTTPS'):
        fetch_json('http://pypi.org/pypi/example/json')
    with pytest.raises(ValueError, match='allow-listed'):
        fetch_json('https://example.com/releases/latest')


def test_corrupt_or_linked_state_fails_closed_instead_of_overwriting(tmp_path):
    root = (tmp_path / 'state').resolve(); maintenance = root / 'maintenance'; maintenance.mkdir(parents=True)
    state = maintenance / 'dependency-updates.json'; state.write_text('{bad json', encoding='utf-8')
    with pytest.raises(DependencyUpdateStateError):
        check_if_due(ROOT, root, environ={}, fetcher=_fetch, installed_version=_installed)


def test_dependency_update_module_has_no_activation_or_package_install_path():
    source = (ROOT / 'update_center/dependency_check.py').read_text(encoding='utf-8')
    forbidden = ('subprocess.', 'pip install', 'npm install', 'os.exec', 'service restart')
    assert not any(item in source for item in forbidden)


def test_scheduler_surfaces_new_updates_without_granting_update_authority(tmp_path):
    from application.composition import default_registry
    from database.store import Store
    from scheduler import AutonomousScheduler

    store = Store(tmp_path / 'worker.db')
    def checker(source_root, state_root, *, environ):
        assert Path(source_root) == ROOT
        assert Path(state_root) == tmp_path
        return {
            'status': 'CHECKED', 'cadence_hours': 24, 'errors': 0,
            'updates': [{'name': 'runtime-a'}], 'new_updates': [{'name': 'runtime-a'}],
        }
    scheduler = AutonomousScheduler(store, registry=default_registry(), dependency_checker=checker)
    summary = scheduler._dependency_update_check()
    assert summary == {'status': 'CHECKED', 'cadence_hours': 24, 'updates': 1, 'new_updates': 1, 'errors': 0}
    with store._connect() as con:
        rows = con.execute("SELECT title,body,severity FROM notifications WHERE title='Dependency updates available'").fetchall()
    assert len(rows) == 1
    assert rows[0]['severity'] == 'INFO'
    assert 'Review qualification evidence before any change.' in rows[0]['body']


def test_scheduler_dependency_metadata_failure_does_not_stop_other_scheduler_work(tmp_path):
    from application.composition import default_registry
    from database.store import Store
    from scheduler import AutonomousScheduler

    store = Store(tmp_path / 'worker.db')
    def broken(*_args, **_kwargs):
        raise OSError('private upstream detail')
    scheduler = AutonomousScheduler(store, registry=default_registry(), dependency_checker=broken)
    assert scheduler._dependency_update_check() == {'status': 'REVIEW', 'updates': 0, 'new_updates': 0, 'errors': 1}
    with store._connect() as con:
        row = con.execute("SELECT details FROM audit_log WHERE action='Dependency update discovery requires review' ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert 'private upstream detail' not in row['details']
