"""Local, human-authorized candidate regression execution; never activation.

Reviewed publisher code executes as the local user. The clean environment and
synthetic fixtures are data isolation, not an OS sandbox for hostile Python.
No HTTP input may supply plans, paths, policy packs or test outcomes directly.
"""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid
import xml.etree.ElementTree as ET

from identity.service import IdentityService
from operations import backup
from operations.time_integrity import utc_now, utc_text
from policy.contracts import ActionRisk, Outcome, PolicyRequest
from update_center.contracts import UpdatePlan
from update_center.managed import ManagedInstallation, _write
from update_center.release_history import verify_and_record

PREFIX = 'update_acceptance_v1:'


def _results(path, required):
    with backup._plain(path, file=True).open('rb') as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Invalid bounded test report.')
    root = ET.fromstring(raw)
    counts = dict(passed=0, failed=0, skipped=0)
    outcomes = {name: [] for name in required}
    for case in root.iter('testcase'):
        name = case.get('file', '').replace('\\', '/')
        status = 'failed' if case.find('failure') is not None or case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
        counts[status] += 1
        if name in outcomes:
            outcomes[name].append(status)
    passed = bool(counts['passed']) and not counts['failed'] and not counts['skipped']
    passed = passed and all(values and set(values) == {'passed'} for values in outcomes.values())
    return counts, passed


def run_candidate(installation, plan, store, principal, policy, publisher_keys, *, evidence_parent,
                  confirm_reviewed_code=False, timeout=1800, minimum_sequence=0, now=None):
    """Produce server-owned evidence from a fixed command, not submitted results.

    Required tests with skips do not qualify. Model/benchmark qualifications need
    their own runner and are blocked here, never treated as implicit passes.
    This receipt is one prerequisite only; it cannot approve or select a source.
    """
    identity = IdentityService(store)
    identity.authorize(principal, 'installation.manage', sensitive=True)
    if confirm_reviewed_code is not True:
        raise PermissionError('Explicit approval to execute reviewed publisher tests is required.')
    decision = policy.evaluate(PolicyRequest('chief.update.validate', ActionRisk.HIGH_IMPACT))
    if decision.decision == Outcome.BLOCK:
        raise PermissionError('Policy blocks candidate execution.')
    if not isinstance(plan, UpdatePlan) or plan.blockers or plan.migration_ids or plan.required_models or plan.required_benchmarks:
        raise ValueError('Candidate needs unresolved qualification or migration gates.')
    if type(timeout) is not int or not 1 <= timeout <= 7200:
        raise ValueError('Bounded test timeout required.')
    current = installation.selection()
    if ManagedInstallation(installation.root).config != installation.config:
        raise ValueError('Managed runtime configuration changed.')
    source, _ = installation.source(plan.candidate_source)
    if plan.candidate_source == current:
        raise ValueError('Candidate validation requires a different staged source.')
    envelope_path = backup._plain(source.parent / 'envelope.json', file=True)
    with envelope_path.open('rb') as stream:
        envelope = stream.read(256 * 1024 + 1)
    release = verify_and_record(store, envelope, publisher_keys, minimum_sequence=minimum_sequence, now=now)
    if release.manifest.source_tree_sha256 != plan.candidate_source:
        raise ValueError('Publisher envelope does not describe this candidate.')
    required = plan.required_tests
    if not required or len(set(required)) != len(required):
        raise ValueError('Unique required test files must be specified.')
    for name in required:
        path = backup._relative(name)
        if not name.startswith('tests/') or not name.endswith('.py'):
            raise ValueError('Only explicit source test files are supported.')
        backup._plain(source / path, file=True)
    parent = backup._plain(evidence_parent)
    if parent == installation.root or parent.is_relative_to(installation.root):
        raise ValueError('Test evidence must remain outside managed source storage.')
    run_id = uuid.uuid4().hex
    directory = parent / ('update-tests-' + run_id)
    directory.mkdir(mode=0o700)
    configuration = directory / 'pytest.ini'
    _write(configuration, b'[pytest]\n')
    report = directory / 'results.xml'
    started = utc_text(utc_now())
    environment = {k: os.environ[k] for k in ('SystemRoot', 'WINDIR', 'LD_LIBRARY_PATH') if k in os.environ}
    environment.update(HOME=str(directory), USERPROFILE=str(directory), TEMP=str(directory), TMP=str(directory),
                       PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONUTF8='1')
    command = [installation.config['python'], '-B', '-E', '-s', '-m', 'pytest',
               '-c', str(configuration), '--rootdir', str(source), '-p', 'no:cacheprovider',
               '-o', 'junit_family=legacy', '--basetemp', str(directory / 'temporary'),
               '--junitxml', str(report), '-q', *required]
    identity.event(principal, 'UPDATE_TESTS_STARTED', resource=run_id, outcome='RECORDED')
    status, counts = 'FAILED', dict(passed=0, failed=0, skipped=0)
    with (directory / 'private-output.log').open('xb') as output:
        os.chmod(directory / 'private-output.log', 0o600)
        try:
            result = subprocess.run(command, cwd=source, env=environment, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=output, timeout=timeout, check=False)
            counts, passing = _results(report, required)
            if result.returncode == 0 and passing:
                status = 'PASSED'
        except subprocess.TimeoutExpired:
            status = 'TIMED_OUT'
        except (OSError, ValueError, ET.ParseError):
            status = 'INVALID_REPORT'
    installation.source(plan.candidate_source)
    verify_and_record(store, envelope, publisher_keys, minimum_sequence=minimum_sequence, now=now)
    if installation.selection() != current:
        raise ValueError('Selected source changed during candidate validation.')
    receipt = dict(version=1, run_id=run_id, status=status, current_source=current,
                   candidate_source=plan.candidate_source, plan_id=plan.plan_id,
                   envelope_sha256=release.envelope_sha256,
                   plan_sha256=hashlib.sha256(json.dumps(asdict(plan), sort_keys=True).encode()).hexdigest(),
                   required_tests=list(required), counts=counts, started_at=started,
                   finished_at=utc_text(utc_now()), human_id=principal.id,
                   activation_authorized=False, deployment_certified=False)
    raw = json.dumps(receipt, sort_keys=True)
    _write(directory / 'receipt.json', raw.encode())
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        # A long test run may outlast reauthentication. Its results can still be
        # recorded; any later approval must independently reauthenticate and
        # recheck revocation. This receipt never carries execution authority.
        con.execute('INSERT INTO control_state(key,value) VALUES(?,?)', (PREFIX + run_id, raw))
        identity._event(con, principal, 'UPDATE_TESTS_FINISHED', None, run_id, status)
    return receipt
