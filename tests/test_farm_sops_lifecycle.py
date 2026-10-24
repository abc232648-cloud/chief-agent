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
