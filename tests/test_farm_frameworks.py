"""Tests for Farm Frameworks, Country Packs, and Gap Analysis."""
import pytest
from domains.farming import frameworks, sops
from tests.test_farm_tasks import staff


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
    _, _, _, _, _, manager, _, worker, _, _, _ = staff(d)

    # Initial gap analysis with zero SOPs
    gap1 = frameworks.gap_analysis(d.store, worker, country_pack='nigeria')
    assert gap1['summary']['total_requirements'] >= 5
    assert gap1['summary']['covered'] == 0
    assert gap1['summary']['missing_evidence'] >= 5
    assert gap1['summary']['preparation_readiness_pct'] == 0.0
    assert len(gap1['certification_preparation_pathway']) == 5
    assert 'No automatic or legal certification' in gap1['notice']

    # Seed starter drafts
    sops.seed_starter_drafts(d.store, manager)

    # Gap analysis now shows IN_PROGRESS drafts
    gap2 = frameworks.gap_analysis(d.store, worker, country_pack='nigeria')
    assert gap2['summary']['in_progress'] >= 3

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

    # Gap analysis now shows covered requirement
    gap3 = frameworks.gap_analysis(d.store, worker, country_pack='nigeria')
    assert gap3['summary']['covered'] >= 1
    assert gap3['summary']['preparation_readiness_pct'] > 0.0

    # Find the FMARD-GAP-01 requirement in the output
    req_item = next(r for r in gap3['requirements'] if r['req_id'] == 'FMARD-GAP-01')
    assert req_item['status'] == 'COVERED'
    assert len(req_item['covered_by_active_sops']) == 1
    assert req_item['covered_by_active_sops'][0]['sop_id'] == 'poultry-water-system'
