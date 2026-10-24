"""Comprehensive tests for Farm SOP lifecycle, drafts, human reviews, attachments, withdrawal, and deletion."""
import base64
import uuid
import pytest

from domains.farming import sops, tasks, frameworks
from tests.test_farm_tasks import create_task, staff
from tests.test_identity_http import request


def ident():
    return str(uuid.uuid4())


def test_draft_lifecycle_and_human_review(dashboard):
    d = dashboard
    _, _, _, _, _, manager, _, worker, _, _, _ = staff(d)

    draft_id = 'draft-pen-cleaning-01'

    # Worker cannot create draft
    with pytest.raises(PermissionError, match='management authority'):
        sops.draft_save(d.store, worker, {
            'draft_id': draft_id, 'sop_id': 'pen-cleaning', 'version': '1.0.0',
            'title': 'Pen Cleaning Routine', 'purpose': 'Clean laying pen.',
            'steps': ['Sweep litter.'], 'warnings': ['Wear mask.']
        })

    # Manager creates draft
    res = sops.draft_save(d.store, manager, {
        'draft_id': draft_id, 'sop_id': 'pen-cleaning', 'version': '1.0.0',
        'title': 'Pen Cleaning Routine', 'purpose': 'Clean laying pen.',
        'steps': ['Sweep litter.', 'Disinfect perches.'], 'warnings': ['Wear mask.'],
        'framework_mappings': ['FMARD-GAP-01']
    })
    assert res['status'] == 'RECORDED'
    assert res['sop_ref'] == 'pen-cleaning@1.0.0'

    # List drafts
    drafts = sops.draft_list(d.store, manager)
    assert drafts['total'] == 1
    assert drafts['drafts'][0]['review_status'] == 'PENDING_REVIEW'

    # Human review: Needs Revision
    rev1 = sops.draft_review(d.store, manager, {
        'draft_id': draft_id,
        'decision': 'NEEDS_REVISION',
        'reviewer_notes': 'Add specific disinfectant dilution ratio.'
    })
    assert rev1['decision'] == 'NEEDS_REVISION'

    # Update draft
    sops.draft_save(d.store, manager, {
        'draft_id': draft_id, 'sop_id': 'pen-cleaning', 'version': '1.0.0',
        'title': 'Pen Cleaning Routine', 'purpose': 'Clean laying pen.',
        'steps': ['Sweep litter.', 'Dilute disinfectant 1:200 and spray perches.'],
        'warnings': ['Wear mask.', 'Ensure birds are not present during spraying.'],
        'framework_mappings': ['FMARD-GAP-01']
    })

    # Human review: Approved
    rev2 = sops.draft_review(d.store, manager, {
        'draft_id': draft_id,
        'decision': 'APPROVED',
        'reviewer_notes': 'Dilution ratio specified. Ready for publication.'
    })
    assert rev2['decision'] == 'APPROVED'

    # Publish the approved procedure
    pub_res = sops.publish(d.store, manager, {
        'event_id': ident(),
        'operation': 'PUBLISH',
        'sop_id': 'pen-cleaning',
        'version': '1.0.0',
        'title': 'Pen Cleaning Routine',
        'purpose': 'Clean laying pen.',
        'steps': ['Sweep litter.', 'Dilute disinfectant 1:200 and spray perches.'],
        'warnings': ['Wear mask.', 'Ensure birds are not present during spraying.'],
        'framework_mappings': ['FMARD-GAP-01']
    })
    assert pub_res['status'] == 'RECORDED'


def test_general_procedure_library_accessible_to_all_workers(dashboard):
    d = dashboard
    _, _, _, _, _, manager, _, worker, other, _, supervisor = staff(d)

    # Publish an SOP
    sops.publish(d.store, manager, {
        'event_id': ident(),
        'operation': 'PUBLISH',
        'sop_id': 'general-handwash',
        'version': '1.0.0',
        'title': 'Handwashing Protocol',
        'purpose': 'Prevent pathogen transmission.',
        'steps': ['Wet hands with clean water.', 'Apply soap for 20 seconds.', 'Dry with paper towel.'],
        'warnings': ['Do not touch clean surfaces with unwashed hands.']
    })

    # Worker can query the general library even without an assigned task
    lib_worker = sops.library(d.store, worker)
    assert lib_worker['total'] >= 1
    titles = [item['title'] for item in lib_worker['library']]
    assert 'Handwashing Protocol' in titles

    # Supervisor and Manager can also view
    assert sops.library(d.store, supervisor)['total'] >= 1
    assert sops.library(d.store, manager)['total'] >= 1


def test_starter_drafts_seeding(dashboard):
    d = dashboard
    _, _, _, _, _, manager, _, _, _, _, _ = staff(d)

    seed_res = sops.seed_starter_drafts(d.store, manager)
    assert seed_res['status'] == 'SEEDED'
    assert seed_res['count'] == 5

    drafts = sops.draft_list(d.store, manager)
    sop_ids = {d['sop_id'] for d in drafts['drafts']}
    assert 'poultry-water-system' in sop_ids
    assert 'poultry-lighting-ventilation' in sop_ids
    assert 'poultry-biosecurity-barrier' in sop_ids
    assert 'poultry-veterinary-escalation' in sop_ids
    assert 'poultry-medication-tracking' in sop_ids


def test_withdrawal_and_owner_only_deletion_controls(dashboard):
    d = dashboard
    _, worker_id, _, supervisor_id, _, manager, _, worker, _, _, _ = staff(d)

    from identity.service import IdentityService
    srv = IdentityService(d.store)
    srv.create_user(d.credentials['principal'], 'dedicated-farm-owner', 'Password123!', 'Owner', ('farming',))
    _, owner = srv.login('dedicated-farm-owner', 'Password123!')

    # Publish an SOP
    sops.publish(d.store, manager, {
        'event_id': ident(),
        'operation': 'PUBLISH',
        'sop_id': 'heater-check',
        'version': '1.0.0',
        'title': 'Brooder Heater Check',
        'purpose': 'Verify gas brooder operation.',
        'steps': ['Check gas pressure regulator.', 'Ignite burner pilot.'],
        'warnings': ['If gas smell is detected, shut main valve immediately.']
    })

    # Assign to worker
    task = create_task(worker_id, supervisor_id, sop_ref='heater-check@1.0.0')
    tasks.append(d.store, manager, task)

    # Worker sees it in assigned
    assert sops.assigned(d.store, worker)['total'] == 1

    # Manager withdraws the SOP
    with_res = sops.withdraw(d.store, manager, {
        'event_id': ident(),
        'sop_id': 'heater-check',
        'version': '1.0.0',
        'reason': 'Replaced by automated heating controller.'
    })
    assert with_res['status'] == 'RECORDED'

    # In general library, it is marked WITHDRAWN
    lib = sops.library(d.store, worker)
    heater = next(i for i in lib['library'] if i['sop_id'] == 'heater-check')
    assert heater['status'] == 'WITHDRAWN'

    # Historical task still has access to the exact instructions
    assert sops.assigned(d.store, worker)['total'] == 1

    # Worker or Manager cannot permanently delete
    with pytest.raises(PermissionError, match='Farm-scoped Owner'):
        sops.delete_sop(d.store, manager, {
            'event_id': ident(),
            'sop_id': 'heater-check',
            'version': '1.0.0',
            'confirm_permanent_deletion': True
        })

    # Owner permanently deletes
    del_res = sops.delete_sop(d.store, owner, {
        'event_id': ident(),
        'sop_id': 'heater-check',
        'version': '1.0.0',
        'confirm_permanent_deletion': True
    })
    assert del_res['status'] == 'DELETED'
    assert 'historical tombstone' in del_res['notice']

    # Now it is tombstoned in catalog and hidden from library
    lib_after = sops.library(d.store, worker)
    assert not any(i['sop_id'] == 'heater-check' for i in lib_after['library'])


def test_safe_attachment_upload_and_download(dashboard):
    d = dashboard
    _, _, _, _, _, manager, _, worker, _, _, _ = staff(d)

    # Create dummy valid text attachment (simulating PDF/TXT document)
    content = b"Standard operating procedure attachment text."
    encoded = base64.b64encode(content).decode('ascii')

    up_res = sops.upload_attachment(d.store, manager, 'poultry-water', '1.0.0', 'water-testing.txt', encoded)
    assert up_res['status'] == 'UPLOADED'
    att_id = up_res['attachment']['attachment_id']
    assert up_res['attachment']['filename'] == 'water-testing.txt'

    # Retrieve attachment
    data, mime, fname = sops.get_attachment(d.store, worker, att_id)
    assert data == content
    assert mime == 'text/plain'
    assert fname == 'water-testing.txt'


def test_concurrent_draft_edit_conflict_detection(dashboard):
    d = dashboard
    _, _, _, _, _, manager, _, _, _, _, _ = staff(d)

    draft_id = 'draft-concurrency-01'
    # Initial draft save -> revision 1
    res1 = sops.draft_save(d.store, manager, {
        'draft_id': draft_id, 'sop_id': 'fan-service', 'version': '1.0.0',
        'title': 'Exhaust Fan Service', 'purpose': 'Clean fans.',
        'steps': ['Turn off fan breaker.'], 'warnings': ['Ensure locked out.']
    })
    assert res1['revision'] == 1

    # Concurrent save with outdated expected_revision 0 should raise conflict
    with pytest.raises(ValueError, match='Concurrent edit conflict: draft revision mismatch.'):
        sops.draft_save(d.store, manager, {
            'draft_id': draft_id, 'sop_id': 'fan-service', 'version': '1.0.0',
            'title': 'Exhaust Fan Service Conflicting Edit', 'purpose': 'Clean fans.',
            'steps': ['Conflicting steps.'], 'warnings': [],
            'expected_revision': 0
        })

    # Save with matching expected_revision 1 succeeds -> revision 2
    res2 = sops.draft_save(d.store, manager, {
        'draft_id': draft_id, 'sop_id': 'fan-service', 'version': '1.0.0',
        'title': 'Exhaust Fan Service Updated', 'purpose': 'Clean fans.',
        'steps': ['Turn off fan breaker and tag out.', 'Wipe louvers.'], 'warnings': ['Lockout.'],
        'expected_revision': 1
    })
    assert res2['revision'] == 2


def test_stale_review_decision_rejected(dashboard):
    d = dashboard
    _, _, _, _, _, manager, _, _, _, _, _ = staff(d)

    draft_id = 'draft-stale-review-01'
    # Revision 1
    sops.draft_save(d.store, manager, {
        'draft_id': draft_id, 'sop_id': 'egg-sorting', 'version': '1.0.0',
        'title': 'Egg Sorting Routine', 'purpose': 'Sort eggs.',
        'steps': ['Inspect eggs.'], 'warnings': []
    })
    # Revision 2
    sops.draft_save(d.store, manager, {
        'draft_id': draft_id, 'sop_id': 'egg-sorting', 'version': '1.0.0',
        'title': 'Egg Sorting Routine v2', 'purpose': 'Sort eggs.',
        'steps': ['Inspect eggs for hairline cracks.', 'Segregate dirty eggs.'], 'warnings': []
    })

    # Reviewing with stale expected_revision=1 should be rejected
    with pytest.raises(ValueError, match='Stale draft revision; review the updated draft before making a decision.'):
        sops.draft_review(d.store, manager, {
            'draft_id': draft_id,
            'decision': 'APPROVED',
            'reviewer_notes': 'Looks fine.',
            'expected_revision': 1
        })

    # Reviewing with current expected_revision=2 succeeds
    rev = sops.draft_review(d.store, manager, {
        'draft_id': draft_id,
        'decision': 'APPROVED',
        'reviewer_notes': 'Reviewed revision 2 with crack inspection.',
        'expected_revision': 2
    })
    assert rev['reviewed_revision'] == 2


def test_publish_from_draft_workflow_and_unapproved_rejection(dashboard):
    d = dashboard
    _, _, _, _, _, manager, _, _, _, _, _ = staff(d)

    draft_id = 'draft-pub-flow-01'
    sops.draft_save(d.store, manager, {
        'draft_id': draft_id, 'sop_id': 'vaccination-newcastle', 'version': '1.0.0',
        'title': 'Newcastle Vaccination', 'purpose': 'Administer eye-drop vaccine.',
        'steps': ['Reconstitute vial with chilled diluent.'], 'warnings': ['Keep on ice.']
    })

    # Publishing unapproved draft must fail
    with pytest.raises(ValueError, match='Only approved drafts may be published.'):
        sops.publish(d.store, manager, {
            'event_id': ident(),
            'operation': 'PUBLISH',
            'sop_id': 'vaccination-newcastle',
            'version': '1.0.0',
            'title': 'Newcastle Vaccination',
            'purpose': 'Administer eye-drop vaccine.',
            'steps': ['Reconstitute vial with chilled diluent.'],
            'warnings': ['Keep on ice.'],
            'from_draft_id': draft_id
        })

    # Approve draft
    sops.draft_review(d.store, manager, {
        'draft_id': draft_id,
        'decision': 'APPROVED',
        'reviewer_notes': 'Protocol verified with veterinarian.'
    })

    # Publishing approved draft succeeds
    pub = sops.publish(d.store, manager, {
        'event_id': ident(),
        'operation': 'PUBLISH',
        'sop_id': 'vaccination-newcastle',
        'version': '1.0.0',
        'title': 'Newcastle Vaccination',
        'purpose': 'Administer eye-drop vaccine.',
        'steps': ['Reconstitute vial with chilled diluent.'],
        'warnings': ['Keep on ice.'],
        'from_draft_id': draft_id
    })
    assert pub['status'] == 'RECORDED'


def test_draft_attachment_worker_access_denial(dashboard):
    d = dashboard
    _, _, _, _, _, manager, _, worker, _, _, _ = staff(d)

    # Save a draft
    draft_id = 'draft-att-protect-01'
    sops.draft_save(d.store, manager, {
        'draft_id': draft_id, 'sop_id': 'secret-recipe', 'version': '1.0.0',
        'title': 'Internal Feed Formula Draft', 'purpose': 'Restricted formula.',
        'steps': ['Mix grains.'], 'warnings': []
    })

    # Upload attachment to draft SOP
    content = b"Restricted feed composition formula draft."
    encoded = base64.b64encode(content).decode('ascii')
    up = sops.upload_attachment(d.store, manager, 'secret-recipe', '1.0.0', 'formula.txt', encoded)
    att_id = up['attachment']['attachment_id']

    # Worker access must be denied
    with pytest.raises(PermissionError, match='Draft attachments are restricted to farm management.'):
        sops.get_attachment(d.store, worker, att_id)

    # Manager can access
    data, _, _ = sops.get_attachment(d.store, manager, att_id)
    assert data == content


def test_tampered_attachment_content_integrity_rejection(dashboard):
    d = dashboard
    _, _, _, _, _, manager, _, _, _, _, _ = staff(d)

    content = b"Original document bytes."
    encoded = base64.b64encode(content).decode('ascii')
    up = sops.upload_attachment(d.store, manager, 'tamper-test', '1.0.0', 'tamper.txt', encoded)
    att_id = up['attachment']['attachment_id']

    # Locate the file on disk and tamper with it
    storage_dir = d.store.path.resolve().parent / 'sop-attachments'
    found = list(storage_dir.glob(f"{att_id}.*"))
    assert len(found) == 1
    found[0].write_bytes(b"Tampered content malicious bytes!")

    # Retrieval must fail SHA-256 integrity verification
    with pytest.raises(ValueError, match='Attachment integrity verification failed'):
        sops.get_attachment(d.store, manager, att_id)


def test_permanent_deletion_removes_physical_attachment_and_scrubs_content(dashboard):
    d = dashboard
    _, _, _, _, _, manager, _, worker, _, _, _ = staff(d)

    from identity.service import IdentityService
    srv = IdentityService(d.store)
    srv.create_user(d.credentials['principal'], 'farm-owner-scrub', 'Password123!', 'Owner', ('farming',))
    _, owner = srv.login('farm-owner-scrub', 'Password123!')

    # Publish an SOP
    sop_id = 'deletable-sop-01'
    sops.publish(d.store, manager, {
        'event_id': ident(),
        'operation': 'PUBLISH',
        'sop_id': sop_id,
        'version': '1.0.0',
        'title': 'Temporary Procedure with Sensitive Attachment',
        'purpose': 'Short-lived routine.',
        'steps': ['Step 1.'],
        'warnings': []
    })

    # Upload attachment
    content = b"Sensitive operational data."
    encoded = base64.b64encode(content).decode('ascii')
    up = sops.upload_attachment(d.store, manager, sop_id, '1.0.0', 'sensitive.txt', encoded)
    att_id = up['attachment']['attachment_id']

    storage_dir = d.store.path.resolve().parent / 'sop-attachments'
    found = list(storage_dir.glob(f"{att_id}.*"))
    assert len(found) == 1
    assert found[0].exists()

    # Owner permanently deletes the SOP
    del_res = sops.delete_sop(d.store, owner, {
        'event_id': ident(),
        'sop_id': sop_id,
        'version': '1.0.0',
        'confirm_permanent_deletion': True
    })
    assert del_res['status'] == 'DELETED'

    # The physical attachment file must be unlinked from storage
    assert not found[0].exists()

    # Requesting attachment now returns FileNotFoundError
    with pytest.raises(FileNotFoundError, match='permanently deleted|not found'):
        sops.get_attachment(d.store, manager, att_id)

