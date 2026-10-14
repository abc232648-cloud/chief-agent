from copy import deepcopy

import pytest

from application.composition import default_catalogs
from tests.test_update_center import current
from tests.test_update_release_trust import NOW, release
from update_center.compatibility_check import HostEnvironment, assess, observe_host
from update_center.release_trust import ReleaseRejected, verify_release


@pytest.mark.parametrize('os', ['windows', 'linux'])
def test_supported_environment_still_requires_real_acceptance(release, os):
    key, payload, sign, _ = release
    result = assess(sign(payload), [key], current(), default_catalogs().capabilities,
                    minimum_sequence=0, now=NOW, environment=HostEnvironment(os, 'x86_64', '3.12.9'))
    assert result['status'] == 'ACCEPTANCE_REQUIRED'
    assert not result['blockers']
    assert result['plan']['required_tests']
    assert result['plan']['required_consumers']
    for field in ('artifact_verified', 'tests_passed', 'backup_verified', 'activation_authorized', 'installed'):
        assert result[field] is False


@pytest.mark.parametrize('host', [
    HostEnvironment('android', 'x86_64', '3.12.9'),
    HostEnvironment('linux', 'aarch64', '3.12.9'),
    HostEnvironment('windows', 'x86_64', '3.11.9'),
    HostEnvironment('linux', 'x86_64', '3.15.0'),
    HostEnvironment('linux', 'x86_64', '3.12.0', 'pypy'),
    HostEnvironment('linux', 'x86_64', '3.14.0', final_python=False),
    HostEnvironment('linux', 'x86_64', 'unknown'),
])
def test_environment_mismatch_never_claims_compatibility(release, host):
    key, payload, sign, _ = release
    result = assess(sign(payload), [key], current(), default_catalogs().capabilities,
                    minimum_sequence=0, now=NOW, environment=host)
    assert result['status'] == 'BLOCKED'
    assert result['blockers'] and result['activation_authorized'] is False


def test_even_declared_reversible_migration_stays_blocked(release):
    key, payload, sign, _ = release
    payload = deepcopy(payload)
    payload['manifest']['schema_sha256'] = 'd' * 64
    payload['manifest']['migrations'] = [dict(migration_id='migration.fixture', from_schema=current().schema_sha256,
                                             to_schema='d' * 64, requires_backup=True, reversible=True)]
    result = assess(sign(payload), [key], current(), default_catalogs().capabilities,
                    minimum_sequence=0, now=NOW, environment=HostEnvironment('windows', 'x86_64', '3.12.0'))
    assert result['status'] == 'BLOCKED'
    assert 'OPERATIONAL_MIGRATION_NOT_AUTHORIZED' in result['blockers']
    assert result['plan']['backup_required'] is True


@pytest.mark.parametrize('alteration', ['duplicate', 'unknown-os', 'unknown-architecture', 'empty-range', 'bad-version', 'command'])
def test_malformed_signed_targets_rejected(release, alteration):
    key, payload, sign, _ = release
    payload = deepcopy(payload)
    target = payload['targets'][0]
    if alteration == 'duplicate':
        payload['targets'].append(target)
    elif alteration == 'unknown-os':
        target['os'] = 'other'
    elif alteration == 'unknown-architecture':
        target['architecture'] = 'other'
    elif alteration == 'empty-range':
        target['python_before'] = target['python_minimum']
    elif alteration == 'bad-version':
        target['python_before'] = '3.15.0;run something'
    else:
        target['install_command'] = 'run something'
    with pytest.raises(ReleaseRejected):
        verify_release(sign(payload), [key], minimum_sequence=0, now=NOW)


def test_current_host_is_an_observation_not_signed_support_claim():
    host = observe_host()
    assert host.os in {'windows', 'linux', 'unknown'}
    assert host.architecture in {'x86_64', 'aarch64', 'unknown'}
    assert isinstance(host.python, str)
