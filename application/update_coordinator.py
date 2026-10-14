"""Local manual managed-update lifecycle with guarded source rollback.

All filesystem/publisher policy inputs are operator-provisioned, not HTTP fields.
Human review does not grant an agent installation power. Managed service restart
is performed separately and any failed activation can roll back only the exact
completed source transaction after writers are stopped again.
"""
from pathlib import Path
import uuid

from update_center.local_inputs import load_policy, read_bounded
from control.components import ComponentControls
from deployment.launch import configure
from deployment.schema import preflight
from identity.service import IdentityService
from policy.contracts import ActionRisk, Outcome, PolicyRequest
from update_center.acceptance import run_candidate
from update_center.approval import UpdateApprovals
from update_center.compatibility_check import assess
from update_center.planner import plan_update
from update_center.recovery import guarded_recovery
from update_center.release_history import verify_and_record
from update_center.release_trust import MAX_ENVELOPE
from update_center.storage import ExistingUpdateStore
from update_center.test_environment import resolve as test_environment


class ManualUpdater:
    def __init__(self, installation, current_manifest, catalog, policy, *, trust_policy,
                 evidence_parent, backup_parent, restore_parent, test_config=None):
        self.installation, self.current, self.catalog, self.policy = installation, current_manifest, catalog, policy
        self.trust_policy = Path(trust_policy)
        self.test_config = test_config
        self.evidence_parent, self.backup_parent, self.restore_parent = evidence_parent, backup_parent, restore_parent
        self.instance = configure(installation.config['instance_config'])
        self.store = ExistingUpdateStore(self.instance.database, identity_events=True)
        self.identity = IdentityService(self.store)
        self.approvals = UpdateApprovals(self.store, catalog, policy,
                                        environment_identity=lambda:test_environment(self.test_config)[1])

    def _authority(self, principal, action):
        self.identity.authorize(principal, 'installation.manage', sensitive=True)
        controls = ComponentControls(self.store, self.catalog, supported={}, guarded_consumers=())
        if not controls.allowed('chief.update-center', 'chief.update_source_selection'):
            raise PermissionError('Manual source selection is disabled by component/capability controls.')
        if self.policy.evaluate(PolicyRequest(action, ActionRisk.HIGH_IMPACT)).decision == Outcome.BLOCK:
            raise PermissionError('Policy prohibits this update operation.')

    def _gates(self, principal, candidate, *, action):
        self._authority(principal, action)
        current = self.installation.selection()
        if current != self.current.source_tree_sha256:
            raise ValueError('Current source differs from the pinned software baseline.')
        if self.current.schema_sha256 != self.installation.config['schema_sha256']:
            raise ValueError('Current schema identity differs from the installation pin.')
        preflight(self.instance.database, self.current.schema_sha256)
        source, _ = self.installation.source(candidate)
        envelope = read_bounded(source.parent / 'envelope.json', MAX_ENVELOPE)
        keys, floor = load_policy(self.trust_policy)
        release = verify_and_record(self.store, envelope, keys, minimum_sequence=floor)
        if release.manifest.source_tree_sha256 != candidate or candidate == current:
            raise ValueError('Publisher identity differs from the staged candidate.')
        assessment = assess(envelope, keys, self.current, self.catalog, minimum_sequence=floor)
        if assessment['blockers']:
            raise ValueError('Release compatibility or operational migration gate blocks selection.')
        plan = plan_update(self.current, release.manifest, self.catalog)
        return plan, keys, floor

    def validate(self, principal, candidate, *, confirm_reviewed_code=False):
        plan, keys, floor = self._gates(principal, candidate, action='chief.update.validate')
        return run_candidate(self.installation, plan, self.store, principal, self.policy, keys,
            evidence_parent=self.evidence_parent, confirm_reviewed_code=confirm_reviewed_code,
            minimum_sequence=floor, test_config=self.test_config)

    def approve(self, principal, candidate, run_id):
        plan, _, _ = self._gates(principal, candidate, action='chief.update.review')
        return self.approvals.approve(principal, plan, run_id, current_source=self.current.source_tree_sha256)

    def select(self, principal, candidate, approval_id, *, private_storage_confirmed=False):
        plan, _, _ = self._gates(principal, candidate, action='chief.update.select_source')
        source, manifest = self.installation.source(self.current.source_tree_sha256)
        with guarded_recovery(self.instance.database, source_root=source, source_manifest=manifest,
            expected_source=self.current.source_tree_sha256, backup_parent=self.backup_parent,
            restore_parent=self.restore_parent, private_storage_confirmed=private_storage_confirmed) as recovery:
            fresh, _, _ = self._gates(principal, candidate, action='chief.update.select_source')
            if fresh != plan:
                raise ValueError('Update requirements changed during recovery preparation.')
            self.approvals.claim(principal, approval_id, fresh, current_source=self.current.source_tree_sha256)
            try:
                result = self.installation._switch(candidate, expected_current=self.current.source_tree_sha256)
            except BaseException:
                self.identity.event(principal, 'UPDATE_SELECTION_FAILED', resource=approval_id, outcome='REVIEW_REQUIRED')
                raise
            self.identity.event(principal, 'UPDATE_SOURCE_SELECTED', resource=approval_id, outcome='RESTART_REQUIRED')
            return {**result, 'approval_id': approval_id,
                    'backup_manifest_sha256': recovery['backup']['manifest_sha256'],
                    'restore_status': 'QUARANTINED', 'deployment_certified': False}

    def rollback_after_activation_failure(self, principal, candidate, *, private_storage_confirmed=False):
        """Rollback only the exact completed candidate after local services are stopped.

        This is source rollback, not operational-data restore. A new quarantined
        backup/restore drill proves all managed writers are stopped before the
        previous source pointer is restored.
        """
        self._authority(principal, 'chief.update.rollback_after_start_failure')
        if self.installation.selection(for_stop=True) != candidate:
            raise ValueError('Failed activation candidate is not the active managed source.')
        source, manifest = self.installation.source(candidate)
        with guarded_recovery(self.instance.database, source_root=source, source_manifest=manifest,
            expected_source=candidate, backup_parent=self.backup_parent,
            restore_parent=self.restore_parent, private_storage_confirmed=private_storage_confirmed) as recovery:
            self._authority(principal, 'chief.update.rollback_after_start_failure')
            reference = uuid.uuid4().hex
            self.identity.event(principal, 'UPDATE_STARTUP_ROLLBACK_REQUESTED', resource=reference, outcome='RECORDED')
            result = self.installation._rollback_completed(
                expected_previous=self.current.source_tree_sha256, expected_candidate=candidate)
            self.identity.event(principal, 'UPDATE_STARTUP_ROLLED_BACK', resource=reference, outcome='RESTART_REQUIRED')
            return {**result, 'recovery_id': reference,
                    'backup_manifest_sha256': recovery['backup']['manifest_sha256'],
                    'restore_status': 'QUARANTINED', 'deployment_certified': False}

    def recover_previous(self, principal, candidate, *, confirm_recovery=False, private_storage_confirmed=False):
        """Explicitly recover only this baseline's interrupted source transaction."""
        self._authority(principal, 'chief.update.recover_previous')
        if confirm_recovery is not True:
            raise PermissionError('Explicit recovery confirmation is required.')
        from update_center.managed import _read
        previous = self.current.source_tree_sha256
        transaction = _read(self.installation.root / 'transaction.json', 4096)
        if transaction != dict(version=1, phase='SWITCHING', previous=previous, candidate=candidate):
            raise ValueError('Recovery must match the exact interrupted baseline/candidate.')
        if self.current.schema_sha256 != self.installation.config['schema_sha256']:
            raise ValueError('Baseline schema pin differs; source-only recovery is blocked.')
        preflight(self.instance.database, self.current.schema_sha256)
        source, manifest = self.installation.source(previous)
        with guarded_recovery(self.instance.database, source_root=source, source_manifest=manifest,
            expected_source=previous, backup_parent=self.backup_parent, restore_parent=self.restore_parent,
            private_storage_confirmed=private_storage_confirmed) as recovery:
            self._authority(principal, 'chief.update.recover_previous')
            reference = uuid.uuid4().hex
            self.identity.event(principal, 'UPDATE_SOURCE_RECOVERY_REQUESTED', resource=reference, outcome='RECORDED')
            result = self.installation._recover_previous(expected_previous=previous, expected_candidate=candidate)
            self.identity.event(principal, 'UPDATE_PREVIOUS_SOURCE_SELECTED', resource=reference, outcome='RESTART_REQUIRED')
            return {**result, 'recovery_id': reference,
                    'backup_manifest_sha256': recovery['backup']['manifest_sha256'],
                    'restore_status': 'QUARANTINED', 'deployment_certified': False}
