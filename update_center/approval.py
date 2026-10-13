"""Single-use human review of server-owned update evidence, not an executor."""
from dataclasses import asdict
from datetime import timedelta
import hashlib
import json
import uuid

from compatibility.manifest import _unique_object
from control.components import ComponentControls
from identity.service import IdentityService
from operations.time_integrity import aware_utc, utc_now, utc_text
from policy.contracts import ActionRisk, Outcome, PolicyRequest
from update_center.acceptance import PREFIX as ACCEPTANCE_PREFIX

PREFIX = 'update_approval_v1:'


class UpdateApprovals:
    def __init__(self, store, catalog, policy, *, now=utc_now, environment_identity=None):
        self.store, self.catalog, self.policy, self.now = store, catalog, policy, now
        self.identity = IdentityService(store, now=now)
        self.environment_identity = environment_identity

    def _check(self, con, principal, plan, run_id, current_source):
        self.identity.authorize(principal, 'installation.manage', sensitive=True)
        controls = ComponentControls(self.store, self.catalog, supported={}, guarded_consumers=())
        if not controls.allowed('chief.update-center', 'chief.update_planning', con=con):
            raise PermissionError('Update preparation is disabled by component controls.')
        decision = self.policy.evaluate(PolicyRequest('chief.update.review', ActionRisk.HIGH_IMPACT))
        if decision.decision == Outcome.BLOCK:
            raise PermissionError('Policy prohibits this update review.')
        if plan.blockers or plan.migration_ids or plan.required_models or plan.required_benchmarks:
            raise ValueError('Unresolved qualifications cannot be approved.')
        row = con.execute('SELECT value FROM control_state WHERE key=?', (ACCEPTANCE_PREFIX + run_id,)).fetchone()
        if not row:
            raise ValueError('Server-owned candidate acceptance is required.')
        receipt = json.loads(row[0], object_pairs_hook=_unique_object)
        if self.environment_identity is not None and receipt.get('test_environment_sha256') != self.environment_identity():
            raise ValueError('Browser/client inputs differ from the tested environment.')
        digest = hashlib.sha256(json.dumps(asdict(plan), sort_keys=True).encode()).hexdigest()
        if (type(receipt['version']) is not int or receipt['version'] != 1 or receipt['run_id'] != run_id or
            receipt['status'] != 'PASSED' or receipt['plan_id'] != plan.plan_id or
            receipt['plan_sha256'] != digest or receipt['candidate_source'] != plan.candidate_source or
            receipt['current_source'] != current_source or receipt['required_tests'] != list(plan.required_tests) or
            receipt['activation_authorized'] is not False):
            raise ValueError('Acceptance does not match this exact update plan.')
        counts = receipt['counts']
        if (set(counts) != {'passed', 'failed', 'skipped'} or
            any(type(v) is not int or v < 0 for v in counts.values()) or
            counts['passed'] == 0 or counts['failed'] or counts['skipped']):
            raise ValueError('Acceptance is incomplete.')
        finished = aware_utc(receipt['finished_at'])
        if not aware_utc(receipt['started_at']) <= finished or not timedelta(0) <= self.now()-finished <= timedelta(hours=24):
            raise ValueError('Acceptance is stale or has unusable timestamps.')
        return hashlib.sha256(row[0].encode()).hexdigest()

    def approve(self, principal, plan, run_id, *, current_source):
        """Called after an explicit human review; never authorizes an agent.

        The later coordinator must separately verify current publisher trust,
        installation capability, backup/restore locks and source identities.
        """
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            evidence = self._check(con, principal, plan, run_id, current_source)
            reference = uuid.uuid4().hex
            approved_at = self.now()
            value = dict(version=1, id=reference, status='APPROVED', human_id=principal.id,
                         session_id=principal.session_id, current_source=current_source,
                         candidate_source=plan.candidate_source, plan_id=plan.plan_id,
                         run_id=run_id, evidence_sha256=evidence, risk='HIGH_IMPACT',
                         approved_at=utc_text(approved_at), expires_at=utc_text(approved_at+timedelta(minutes=5)))
            con.execute('INSERT INTO control_state(key,value) VALUES(?,?)', (PREFIX+reference, json.dumps(value, sort_keys=True)))
            self.identity._event(con, principal, 'UPDATE_REVIEW_APPROVED', None, reference, 'RECORDED')
        return value

    def claim(self, principal, reference, plan, *, current_source):
        """Coordinator-only one-time claim; all other execution gates remain required."""
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT value FROM control_state WHERE key=?', (PREFIX+reference,)).fetchone()
            if not row:
                raise PermissionError('Update approval is missing.')
            value = json.loads(row[0], object_pairs_hook=_unique_object)
            if (type(value['version']) is not int or value['version'] != 1 or value['id'] != reference or
                value['risk'] != 'HIGH_IMPACT' or value['status'] != 'APPROVED' or value['human_id'] != principal.id or
                value['session_id'] != principal.session_id or value['plan_id'] != plan.plan_id or
                value['current_source'] != current_source or value['candidate_source'] != plan.candidate_source or
                aware_utc(value['expires_at'])-aware_utc(value['approved_at']) != timedelta(minutes=5) or
                not aware_utc(value['approved_at']) <= self.now() < aware_utc(value['expires_at'])):
                raise PermissionError('Approval changed, expired or was already claimed.')
            evidence = self._check(con, principal, plan, value['run_id'], current_source)
            if evidence != value['evidence_sha256']:
                raise PermissionError('Acceptance changed after approval.')
            value.update(status='CLAIMED', claimed_at=utc_text(self.now()))
            con.execute('UPDATE control_state SET value=? WHERE key=?', (json.dumps(value, sort_keys=True), PREFIX+reference))
            self.identity._event(con, principal, 'UPDATE_REVIEW_CLAIMED', None, reference, 'RECORDED')
        return value
