"""Authoritative Farm Frameworks, Standards and Certification-Preparation Engine.

Includes Nigeria-focused first pack (NAFDAC, FMARD) and international foundations
(WOAH poultry biosecurity, Codex Alimentarius CAC/RCP 15-1976 egg hygiene/HACCP,
and NIST/OWASP operational integrity).

Deterministic evidence tracking identifies missing-evidence gaps and structures
a clear certification-preparation pathway. Does not claim or issue formal legal
or clinical certification.
"""
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
import json

from identity.service import IdentityService
from . import setup


@dataclass(frozen=True)
class FrameworkRequirement:
    req_id: str
    framework_id: str
    section: str
    title: str
    description: str
    evidence_needed: str
    recommended_sop_category: str
    mandatory: bool = True


@dataclass(frozen=True)
class Framework:
    id: str
    name: str
    version: str
    authority: str
    country: str
    scope: str
    url_reference: str
    requirements: tuple


NIGERIA_NAFDAC_VMD = Framework(
    id='NAFDAC-VMD-2019',
    name='NAFDAC Veterinary Medicinal Products and Animal Feeds Regulations',
    version='2019.1',
    authority='National Agency for Food and Drug Administration and Control (Nigeria)',
    country='NG',
    scope='Veterinary medicine storage, prescription observance, withdrawal period tracking, feed safety',
    url_reference='https://www.nafdac.gov.ng/regulations-guidelines/veterinary-medicine-guidelines/',
    requirements=(
        FrameworkRequirement(
            req_id='NAFDAC-VMD-01',
            framework_id='NAFDAC-VMD-2019',
            section='Part II, Section 4',
            title='Prescription and Medication Logging',
            description='All veterinary pharmaceuticals administered to commercial poultry must be logged with date, flock batch, administering staff, batch number, and veterinarian authorization.',
            evidence_needed='Task execution log pinned to approved medication SOP with treatment records.',
            recommended_sop_category='medication',
        ),
        FrameworkRequirement(
            req_id='NAFDAC-VMD-02',
            framework_id='NAFDAC-VMD-2019',
            section='Part III, Section 8',
            title='Withdrawal Period Enforcement',
            description='Eggs or meat from treated flocks must not be released for consumption during the statutory withdrawal window.',
            evidence_needed='Active treatment restriction records and flock quarantine checks prior to sale.',
            recommended_sop_category='medication',
        ),
    )
)

NIGERIA_FMARD_GAP = Framework(
    id='FMARD-GAP-2021',
    name='FMARD Good Agricultural Practices for Commercial Poultry',
    version='2021.2',
    authority='Federal Ministry of Agriculture and Rural Development (Nigeria)',
    country='NG',
    scope='Farm sanitation, flock density, daily water availability and mortality disposal',
    url_reference='https://fmard.gov.ng/national-livestock-transformation-plan/',
    requirements=(
        FrameworkRequirement(
            req_id='FMARD-GAP-01',
            framework_id='FMARD-GAP-2021',
            section='Section 3.1 - Water and Nutrition',
            title='Potable Water Access and Line Sanitation',
            description='Flocks must have continuous access to tested potable water. Header tanks and drinker lines must be inspected daily and sanitized on schedule.',
            evidence_needed='Daily water inspection task completions and periodic line sanitization records.',
            recommended_sop_category='water',
        ),
        FrameworkRequirement(
            req_id='FMARD-GAP-02',
            framework_id='FMARD-GAP-2021',
            section='Section 4.3 - Flock Health Monitoring',
            title='Daily Mortality Tracking and Veterinary Escalation',
            description='Daily mortality must be logged per pen. Spikes exceeding threshold (0.5% daily) must trigger immediate veterinary notification.',
            evidence_needed='Daily count records, health incident logs, and veterinary escalation audit trail.',
            recommended_sop_category='escalation',
        ),
    )
)

WOAH_BIOSECURITY = Framework(
    id='WOAH-TAHC-CHAP6.5',
    name='WOAH Terrestrial Animal Health Code - Biosecurity in Poultry Production',
    version='2023 Edition',
    authority='World Organisation for Animal Health (WOAH)',
    country='INTERNATIONAL',
    scope='Preventing introduction and spread of transmissible poultry diseases (Avian Influenza, Newcastle Disease)',
    url_reference='https://www.woah.org/en/what-we-do/standards/codes-and-manuals/terrestrial-code-online-access/?id=169&L=1&htmfile=chapitre_biosecurity_poultry.htm',
    requirements=(
        FrameworkRequirement(
            req_id='WOAH-BIO-01',
            framework_id='WOAH-TAHC-CHAP6.5',
            section='Article 6.5.3 - Personnel and Visitor Controls',
            title='Biosecure Entry, Footwear Disinfection and Protective Apparel',
            description='Restricted entry barrier with vehicle wheel disinfection, boot baths replenished daily, dedicated footwear, and visitor logging.',
            evidence_needed='Daily biosecurity checkpoint logs and visitor register verification.',
            recommended_sop_category='biosecurity',
        ),
        FrameworkRequirement(
            req_id='WOAH-BIO-02',
            framework_id='WOAH-TAHC-CHAP6.5',
            section='Article 6.5.6 - Isolation and Disease Containment',
            title='Flock Quarantine and Movement Restriction Protocol',
            description='Documented procedures for immediate pen isolation, barrier enforcement, and notification upon suspicious clinical signs.',
            evidence_needed='Incident reports, active containment tags, and isolation protocol execution.',
            recommended_sop_category='escalation',
        ),
    )
)

CODEX_EGG_HYGIENE = Framework(
    id='CODEX-CAC-RCP-15',
    name='Codex Code of Hygienic Practice for Eggs and Egg Products',
    version='CAC/RCP 15-1976 (Rev. 2007)',
    authority='Codex Alimentarius Commission (FAO/WHO)',
    country='INTERNATIONAL',
    scope='Primary production hygiene, Salmonella prevention, clean egg handling, temperature and packing integrity',
    url_reference='https://www.fao.org/fao-who-codexalimentarius/codex-texts/codes-of-practice/en/',
    requirements=(
        FrameworkRequirement(
            req_id='CODEX-EGG-01',
            framework_id='CODEX-CAC-RCP-15',
            section='Section 5.2 - Egg Collection and Handling Hygiene',
            title='Collection Frequency, Shell Integrity and Segregation',
            description='Eggs must be collected at least twice daily into clean trays; cracked, soiled or leaking eggs segregated immediately to avoid cross-contamination.',
            evidence_needed='Daily collection logs with egg grading (clean, cracked, reject) and batch tracing.',
            recommended_sop_category='hygiene',
        ),
        FrameworkRequirement(
            req_id='CODEX-EGG-02',
            framework_id='CODEX-CAC-RCP-15',
            section='Section 5.3 - Environmental Sanitation and Pest Control',
            title='Laying House Hygiene and Ventilation Maintenance',
            description='Litter moisture management, manure removal, insect/rodent pest monitoring, and proper ventilation to prevent ammonia buildup.',
            evidence_needed='House ventilation check tasks and weekly pest control logs.',
            recommended_sop_category='ventilation',
        ),
    )
)

NIST_SECURITY_PRIVACY = Framework(
    id='NIST-CSF-DATA',
    name='NIST Operational Security and Data Integrity Guidelines for Local Deployments',
    version='CSF 2.0 / SP 800-171',
    authority='National Institute of Standards and Technology (NIST)',
    country='INTERNATIONAL',
    scope='Local database integrity, offline verification, non-tampering audit records, role isolation',
    url_reference='https://csrc.nist.gov/publications/detail/sp/800-171/rev-2/final',
    requirements=(
        FrameworkRequirement(
            req_id='NIST-INT-01',
            framework_id='NIST-CSF-DATA',
            section='PR.DS-1 - Data-at-Rest and In-Transit Protection',
            title='Immutable Versioning and Private Attachment Isolation',
            description='Published operational procedures and records must be immutable; untrusted file attachments parsed strictly in isolated sandboxed contexts.',
            evidence_needed='Deterministic cryptographic record hashing and sandboxed document validation.',
            recommended_sop_category='security',
        ),
    )
)

# Registry of supported frameworks
FRAMEWORKS: Dict[str, Framework] = {
    NIGERIA_NAFDAC_VMD.id: NIGERIA_NAFDAC_VMD,
    NIGERIA_FMARD_GAP.id: NIGERIA_FMARD_GAP,
    WOAH_BIOSECURITY.id: WOAH_BIOSECURITY,
    CODEX_EGG_HYGIENE.id: CODEX_EGG_HYGIENE,
    NIST_SECURITY_PRIVACY.id: NIST_SECURITY_PRIVACY,
}

COUNTRY_PACKS = {
    'nigeria': {
        'code': 'NG',
        'name': 'Nigeria Commercial Poultry Pack',
        'framework_ids': ['NAFDAC-VMD-2019', 'FMARD-GAP-2021', 'WOAH-TAHC-CHAP6.5', 'CODEX-CAC-RCP-15', 'NIST-CSF-DATA'],
        'default': True,
        'description': 'Aligned with Nigerian regulatory requirements (NAFDAC, FMARD) and core international standards (WOAH, Codex, NIST).'
    },
    'international': {
        'code': 'INTL',
        'name': 'International Baseline Pack',
        'framework_ids': ['WOAH-TAHC-CHAP6.5', 'CODEX-CAC-RCP-15', 'NIST-CSF-DATA'],
        'default': False,
        'description': 'Global baseline standards for poultry biosecurity, egg hygiene, and operational security.'
    }
}


def list_country_packs():
    return list(COUNTRY_PACKS.values())


def list_frameworks(country_pack: Optional[str] = None):
    if country_pack:
        pack = COUNTRY_PACKS.get(country_pack.lower())
        if not pack:
            raise ValueError(f'Unknown country pack: {country_pack}')
        return [asdict(FRAMEWORKS[fid]) for fid in pack['framework_ids'] if fid in FRAMEWORKS]
    return [asdict(fw) for fw in FRAMEWORKS.values()]


def get_requirement(req_id: str) -> Optional[FrameworkRequirement]:
    for fw in FRAMEWORKS.values():
        for req in fw.requirements:
            if req.req_id == req_id:
                return req
    return None


def gap_analysis(store, principal, country_pack: str = 'nigeria'):
    """Analyze compliance preparation posture with strict exact requirement mapping,
    worker draft information shielding, and distinct evidence collection/review stages.
    """
    from . import sops, tasks
    with store._connect() as con:
        setup.authorize(store, con, principal, 'read')
        caller_role = setup.role(con, principal)
        published_sops = list(sops.catalog(sops.rows(con)).values())
        # Restrict draft visibility: Workers cannot see unpublished draft procedures or internal draft pipelines
        is_worker = (caller_role == 'WORKER')
        drafts = [] if is_worker else sops.draft_list_internal(con)
        
        # Load task records to check evidence collection and review
        task_snapshots = tasks._snapshots(tasks.rows(con))

    pack = COUNTRY_PACKS.get(country_pack.lower(), COUNTRY_PACKS['nigeria'])
    active_reqs: List[FrameworkRequirement] = []
    for fid in pack['framework_ids']:
        fw = FRAMEWORKS.get(fid)
        if fw:
            active_reqs.extend(fw.requirements)

    mapped_evidence = {req.req_id: [] for req in active_reqs}
    draft_evidence = {req.req_id: [] for req in active_reqs}

    for req in active_reqs:
        # Require exact requirement ID mapping; whole-framework wildcard mapping is strictly prohibited
        for sop in published_sops:
            mappings = sop.get('framework_mappings') or []
            if req.req_id in mappings:
                mapped_evidence[req.req_id].append({
                    'sop_id': sop['sop_id'],
                    'version': sop['version'],
                    'title': sop['title'],
                    'status': sop.get('status', 'ACTIVE')
                })

        for draft in drafts:
            mappings = draft.get('payload', {}).get('framework_mappings') or []
            if req.req_id in mappings:
                draft_evidence[req.req_id].append({
                    'draft_id': draft['payload']['draft_id'],
                    'sop_id': draft['payload']['sop_id'],
                    'version': draft['payload']['version'],
                    'title': draft['payload']['title'],
                    'review_status': draft['payload'].get('review_status', 'DRAFT')
                })

    analysis = []
    covered_count = 0
    in_progress_count = 0
    missing_count = 0

    for req in active_reqs:
        active_cov = [s for s in mapped_evidence[req.req_id] if s['status'] == 'ACTIVE']
        draft_cov = draft_evidence[req.req_id] if not is_worker else []

        sop_mapped = len(active_cov) > 0
        evidence_collected = False
        evidence_reviewed = False

        if sop_mapped:
            # Check if any tasks reference active SOPs for this requirement
            active_sop_refs = {f"{s['sop_id']}@{s['version']}" for s in active_cov} | {s['sop_id'] for s in active_cov}
            matching_tasks = [
                t for t in task_snapshots.values()
                if t.get('sop_ref') in active_sop_refs and t.get('status') in {'AWAITING_VERIFICATION', 'VERIFIED', 'IN_PROGRESS'}
            ]
            if matching_tasks:
                evidence_collected = True
                # Evidence is reviewed when supervisor has formally verified the completed task
                verified_tasks = [t for t in matching_tasks if t.get('status') == 'VERIFIED']
                if verified_tasks:
                    evidence_reviewed = True

        if sop_mapped and evidence_collected and evidence_reviewed:
            status = 'COVERED'
            readiness_stage = 'AUDIT_READY'
            covered_count += 1
            recommended_action = 'Evidence verified; ready for regulatory/certification audit review.'
        elif sop_mapped and evidence_collected:
            status = 'COVERED'
            readiness_stage = 'EVIDENCE_COLLECTED'
            covered_count += 1
            recommended_action = 'Task evidence logged; conduct manager review and audit sign-off.'
        elif sop_mapped:
            status = 'COVERED'
            readiness_stage = 'PROCEDURE_MAPPED'
            covered_count += 1
            recommended_action = 'Procedure active; assign and record field execution tasks to collect compliance evidence.'
        elif draft_cov:
            status = 'IN_PROGRESS'
            readiness_stage = 'IN_PROGRESS'
            in_progress_count += 1
            recommended_action = f'Complete human review and publish draft {draft_cov[0]["sop_id"]}@{draft_cov[0]["version"]}.'
        else:
            status = 'MISSING_EVIDENCE'
            readiness_stage = 'UNMAPPED'
            missing_count += 1
            recommended_action = f'Create and publish an SOP for {req.recommended_sop_category} satisfying: {req.title}.'

        item = {
            'req_id': req.req_id,
            'framework_id': req.framework_id,
            'section': req.section,
            'title': req.title,
            'description': req.description,
            'evidence_needed': req.evidence_needed,
            'sop_mapped': sop_mapped,
            'evidence_collected': evidence_collected,
            'evidence_reviewed': evidence_reviewed,
            'readiness_stage': readiness_stage,
            'status': status,
            'covered_by_active_sops': active_cov,
            'recommended_action': recommended_action,
        }
        if not is_worker:
            item['covered_by_drafts'] = draft_cov
        analysis.append(item)

    total = len(active_reqs)
    readiness_percentage = round((covered_count / total * 100), 1) if total > 0 else 0.0

    return {
        'country_pack': pack['name'],
        'country_code': pack['code'],
        'role_view': caller_role,
        'summary': {
            'total_requirements': total,
            'covered': covered_count,
            'in_progress': in_progress_count,
            'missing_evidence': missing_count,
            'preparation_readiness_pct': readiness_percentage,
        },
        'certification_preparation_pathway': [
            {'stage': 1, 'name': 'Draft Operational Procedures', 'detail': 'Author or adapt starter drafts for missing biosecurity, water, medication, and ventilation routines.'},
            {'stage': 2, 'name': 'Manager/Owner Human Review', 'detail': 'Formally review each draft in Chief console with reviewer notes before publication.'},
            {'stage': 3, 'name': 'Publish Immutable Versions', 'detail': 'Publish reviewed procedures with pinned semantic versions (e.g. 1.0.0).'},
            {'stage': 4, 'name': 'Assign and Execute Field Tasks', 'detail': 'Dispatch daily tasks referencing exact procedure versions; record completions with required evidence.'},
            {'stage': 5, 'name': 'Supervisor Evidence Review & Audit Export', 'detail': 'Supervisors review and sign off on completed task evidence logs for external auditor or regulatory inspector.'}
        ],
        'requirements': analysis,
        'notice': 'This analysis provides deterministic evidence tracking for certification preparation. No automatic or legal certification is granted by this system.'
    }
