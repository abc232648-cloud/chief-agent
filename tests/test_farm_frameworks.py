"""Tests for Farm Frameworks, Country Packs, and Gap Analysis."""
import pytest
from domains.farming import frameworks, sops, tasks
from tests.test_farm_tasks import staff, create_task, event


def test_framework_catalog_and_country_packs(dashboard):
    packs = frameworks.list_country_packs()
    pack_codes = {p['code'] for p in packs}
    assert 'NG' in pack_codes
    assert 'INTL' in pack_codes

    ng_frameworks = frameworks.list_frameworks(country_pack='nigeria')
    fw_ids = {fw['id'] for fw in ng_frameworks}
    assert 'NAFDAC-VMD-2019' in fw_ids
    assert 'FMARD-GAP-2021' in fw_ids
    assert 'WOAH-TAHC-CHAP6.5' in fw_ids
    assert 'CODEX-CAC-RCP-15' in fw_ids
    assert 'NIST-CSF-DATA' in fw_ids

    # Requirements exist
    req = frameworks.get_requirement('NAFDAC-VMD-01')
    assert req is not None
    assert 'Prescription and Medication Logging' in req.title


def test_gap_analysis_and_certification_preparation(dashboard):
    d = dashboard
    _, worker_id, _, supervisor_id, _, manager, _, worker, _, _, supervisor = staff(d)

    # Initial gap analysis with zero SOPs
    gap1 = frameworks.gap_analysis(d.store, manager, country_pack='nigeria')
    assert gap1['summary']['total_requirements'] >= 5
    assert gap1['summary']['covered'] == 0
    assert gap1['summary']['missing_evidence'] >= 5
    assert gap1['summary']['preparation_readiness_pct'] == 0.0
    assert len(gap1['certification_preparation_pathway']) == 5
    assert 'No automatic or legal certification' in gap1['notice']

    # Seed starter drafts
    sops.seed_starter_drafts(d.store, manager)

    # Manager view shows IN_PROGRESS drafts with draft details
    gap2_mgr = frameworks.gap_analysis(d.store, manager, country_pack='nigeria')
    assert gap2_mgr['summary']['in_progress'] >= 3
    in_prog_reqs = [r for r in gap2_mgr['requirements'] if r['status'] == 'IN_PROGRESS']
    assert len(in_prog_reqs) >= 3
    assert all('covered_by_drafts' in r and len(r['covered_by_drafts']) > 0 for r in in_prog_reqs)

    # Worker view restricts draft visibility: no draft pipelines or draft details leaked
    gap2_wkr = frameworks.gap_analysis(d.store, worker, country_pack='nigeria')
    assert gap2_wkr['summary']['in_progress'] == 0
    assert all('covered_by_drafts' not in r for r in gap2_wkr['requirements'])
    assert all(r['status'] == 'MISSING_EVIDENCE' for r in gap2_wkr['requirements'])

    # Publish one draft satisfying FMARD-GAP-01
    sops.publish(d.store, manager, {
        'event_id': 'evt-gap-pub-01',
        'operation': 'PUBLISH',
        'sop_id': 'poultry-water-system',
        'version': '1.0.0',
        'title': 'Daily Poultry Water-Line Inspection and Sanitization',
        'purpose': 'Potable water access and line sanitation.',
        'steps': ['Inspect header tank.', 'Sanitize lines.'],
        'warnings': ['Wear gloves.'],
        'framework_mappings': ['FMARD-GAP-01']
    })

    # Gap analysis now shows covered requirement with PROCEDURE_MAPPED stage
    gap3 = frameworks.gap_analysis(d.store, worker, country_pack='nigeria')
    assert gap3['summary']['covered'] >= 1
    assert gap3['summary']['preparation_readiness_pct'] > 0.0

    # Find the FMARD-GAP-01 requirement in the output
    req_item = next(r for r in gap3['requirements'] if r['req_id'] == 'FMARD-GAP-01')
    assert req_item['status'] == 'COVERED'
    assert req_item['sop_mapped'] is True
    assert req_item['evidence_collected'] is False
    assert req_item['readiness_stage'] == 'PROCEDURE_MAPPED'
    assert len(req_item['covered_by_active_sops']) == 1
    assert req_item['covered_by_active_sops'][0]['sop_id'] == 'poultry-water-system'

    # Now create and execute a task referencing this SOP
    task = create_task(worker_id, supervisor_id, sop_ref='poultry-water-system@1.0.0')
    tasks.append(d.store, manager, task)

    # Worker acknowledges, starts, and submits task with evidence note
    ack = event('ACKNOWLEDGE', task['task_id'], task['event_id'])
    tasks.append(d.store, worker, ack)
    start = event('START', task['task_id'], ack['event_id'])
    tasks.append(d.store, worker, start)
    submit = event('SUBMIT', task['task_id'], start['event_id'], note='Checked header tanks and flushed drinker lines. Pressure optimal at 2.5 bar.')
    tasks.append(d.store, worker, submit)

    # Check gap analysis: evidence_collected is True, stage advances to EVIDENCE_COLLECTED
    gap4 = frameworks.gap_analysis(d.store, manager, country_pack='nigeria')
    req_item4 = next(r for r in gap4['requirements'] if r['req_id'] == 'FMARD-GAP-01')
    assert req_item4['evidence_collected'] is True
    assert req_item4['evidence_reviewed'] is False
    assert req_item4['readiness_stage'] == 'EVIDENCE_COLLECTED'

    # Supervisor verifies the task execution
    verify = event('VERIFY', task['task_id'], submit['event_id'], note='Verified water pressure gauge logs and sanitizer concentration readings.')
    tasks.append(d.store, supervisor, verify)

    # Check gap analysis: evidence_reviewed is True, stage advances to AUDIT_READY
    gap5 = frameworks.gap_analysis(d.store, manager, country_pack='nigeria')
    req_item5 = next(r for r in gap5['requirements'] if r['req_id'] == 'FMARD-GAP-01')
    assert req_item5['evidence_collected'] is True
    assert req_item5['evidence_reviewed'] is True
    assert req_item5['readiness_stage'] == 'AUDIT_READY'


def test_whole_framework_wildcard_mapping_is_rejected(dashboard):
    """Ensure whole-framework ID does not automatically satisfy individual requirements."""
    d = dashboard
    _, _, _, _, _, manager, _, _, _, _, _ = staff(d)

    # Publish an SOP with generic framework ID rather than exact requirement ID
    sops.publish(d.store, manager, {
        'event_id': 'evt-fw-wildcard-pub-01',
        'operation': 'PUBLISH',
        'sop_id': 'generic-codex-sop',
        'version': '1.0.0',
        'title': 'Generic Egg Handling Note',
        'purpose': 'General notes referencing framework.',
        'steps': ['Handle eggs.'],
        'warnings': ['Be careful.'],
        'framework_mappings': ['CODEX-CAC-RCP-15']  # Whole framework ID, not CODEX-EGG-01
    })

    gap = frameworks.gap_analysis(d.store, manager, country_pack='nigeria')
    req_egg1 = next(r for r in gap['requirements'] if r['req_id'] == 'CODEX-EGG-01')
    req_egg2 = next(r for r in gap['requirements'] if r['req_id'] == 'CODEX-EGG-02')

    # Neither requirement should be marked COVERED by whole-framework wildcard
    assert req_egg1['sop_mapped'] is False
    assert req_egg1['status'] == 'MISSING_EVIDENCE'
    assert req_egg2['sop_mapped'] is False
    assert req_egg2['status'] == 'MISSING_EVIDENCE'
