"""Versioned Farm field SOPs exposed to Staff through authoritative task scope and general library.

Published SOP versions are immutable. Tasks refer to a pinned ``sop_id@version``;
the assigned endpoint never substitutes a newer version or invents unavailable
instructions.

Supports full lifecycle: drafting, human review, publication, withdrawal,
Owner-only permanent deletion (with historical tombstone preservation), safe PDF
attachment ingestion via sandboxed validation, and framework requirement mapping.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time

from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text
from . import setup, tasks
from .journal import bounded_text, identifier

KIND = 'farm_sop_v1'
DRAFT_KIND = 'farm_sop_draft_v1'
ATTACHMENT_KIND = 'farm_sop_attachment_v1'
LIMIT = 5000
_VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,39}')


def version(value):
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ValueError('Use a 1–40 character SOP version.')
    return value


def reference(sop_id, sop_version):
    return identifier(sop_id) + '@' + version(sop_version)


def parse_reference(value):
    if not isinstance(value, str) or value.count('@') != 1:
        raise ValueError('SOP references must pin an exact sop_id@version.')
    sop_id, sop_version = value.split('@', 1)
    return identifier(sop_id), version(sop_version)


def rows(con):
    result = con.execute(
        "SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id LIMIT ?",
        (KIND, LIMIT + 1),
    ).fetchall()
    if len(result) > LIMIT:
        raise ValueError('SOP catalog capacity requires indexed upgrade; no truncated catalog returned.')
    return [json.loads(row[0]) for row in result]


def draft_rows(con):
    result = con.execute(
        "SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id LIMIT ?",
        (DRAFT_KIND, LIMIT + 1),
    ).fetchall()
    return [json.loads(row[0]) for row in result]


def draft_list_internal(con):
    return draft_rows(con)


def catalog(history):
    result = {}
    for record in history:
        payload = record['payload']
        op = payload.get('operation', 'PUBLISH')
        key = (payload['sop_id'], payload['version'])
        if op == 'DELETE':
            result[key] = {
                'sop_id': payload['sop_id'],
                'version': payload['version'],
                'title': payload.get('title', '[Deleted Procedure]'),
                'purpose': '[This procedure was permanently deleted by the Farm Owner. Historical tasks retain tombstone records.]',
                'steps': [],
                'warnings': [],
                'status': 'DELETED',
                'updated_at': record['received_at'],
                'framework_mappings': [],
                'attachments': [],
            }
        elif op == 'WITHDRAW':
            if key in result:
                result[key]['status'] = 'WITHDRAWN'
                result[key]['withdrawal_reason'] = payload.get('reason', 'Withdrawn by management.')
                result[key]['updated_at'] = record['received_at']
        else:
            result[key] = {
                'sop_id': payload['sop_id'],
                'version': payload['version'],
                'title': payload['title'],
                'purpose': payload['purpose'],
                'steps': list(payload['steps']),
                'warnings': list(payload.get('warnings', [])),
                'status': payload.get('status', 'ACTIVE'),
                'framework_mappings': list(payload.get('framework_mappings', [])),
                'attachments': list(payload.get('attachments', [])),
                'updated_at': record['received_at'],
            }
    return result


def _text_list(value, *, limit, item_limit, name):
    if not isinstance(value, list) or not 1 <= len(value) <= limit:
        raise ValueError(f'{name} must contain between 1 and {limit} entries.')
    return [bounded_text(item, item_limit) for item in value]


def publish(store, principal, payload):
    if not isinstance(payload, dict):
        raise ValueError('Supply an SOP publication.')
    p = dict(payload)
    base_required = {'event_id', 'operation', 'sop_id', 'version', 'title', 'purpose', 'steps', 'warnings'}
    if not base_required.issubset(set(p)) or p.get('operation') != 'PUBLISH':
        raise ValueError('Supply exactly the SOP publication fields.')
    allowed = base_required | {'status', 'framework_mappings', 'attachments', 'notes'}
    if not set(p).issubset(allowed):
        raise ValueError('Unknown fields in SOP publication.')
    identifier(p['event_id']); identifier(p['sop_id']); p['version'] = version(p['version'])
    p['title'] = bounded_text(p['title'], 160)
    p['purpose'] = bounded_text(p['purpose'], 1000)
    p['steps'] = _text_list(p['steps'], limit=40, item_limit=1000, name='SOP steps')
    if not isinstance(p['warnings'], list) or len(p['warnings']) > 20:
        raise ValueError('SOP warnings must be a list with at most 20 entries.')
    p['warnings'] = [bounded_text(item, 800) for item in p['warnings']]
    p['status'] = p.get('status', 'ACTIVE')
    if p['status'] not in {'ACTIVE', 'WITHDRAWN'}:
        raise ValueError('Publication status must be ACTIVE or WITHDRAWN.')
    if 'framework_mappings' in p:
        if not isinstance(p['framework_mappings'], list) or len(p['framework_mappings']) > 20:
            raise ValueError('Framework mappings must be a list of at most 20 identifiers.')
        p['framework_mappings'] = [bounded_text(m, 50) for m in p['framework_mappings']]
    else:
        p['framework_mappings'] = []

    if 'attachments' in p:
        if not isinstance(p['attachments'], list) or len(p['attachments']) > 10:
            raise ValueError('Attachments list cannot exceed 10 items.')
    else:
        p['attachments'] = []

    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = setup.authorize(store, con, principal, 'setup')
        setup.available(store, con)
        history = rows(con)
        prior_event = next((r for r in history if r['payload']['event_id'] == p['event_id']), None)
        if prior_event:
            # Check idempotent match
            if prior_event['payload']['sop_id'] != p['sop_id'] or prior_event['payload']['version'] != p['version'] or prior_event['actor_id'] != principal.id:
                raise ValueError('Submission identifier conflict.')
            return {'status': 'ALREADY_RECORDED', 'sop_ref': reference(p['sop_id'], p['version']),
                    'updated_at': prior_event['received_at']}
        prior_version = next((r for r in history if r['payload']['sop_id'] == p['sop_id'] and r['payload']['version'] == p['version'] and r['payload'].get('operation') == 'PUBLISH'), None)
        if prior_version:
            raise ValueError('Published SOP versions are immutable; publish a new version instead.')
        received_at = utc_text(utc_now())
        record = {'version': 1, 'payload': p, 'actor_id': principal.id,
                  'session_id': principal.session_id, 'received_at': received_at}
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",
                    (KIND, json.dumps(record, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_SOP_PUBLISHED', 'farming', p['event_id'], 'RECORDED')
    return {'status': 'RECORDED', 'sop_ref': reference(p['sop_id'], p['version']), 'updated_at': received_at}


def withdraw(store, principal, payload):
    """Mark a published SOP version as withdrawn. Historical tasks retain exact reference."""
    if not isinstance(payload, dict):
        raise ValueError('Supply a withdrawal payload.')
    required = {'event_id', 'sop_id', 'version', 'reason'}
    if not required.issubset(set(payload)):
        raise ValueError('Supply event_id, sop_id, version, and reason for withdrawal.')
    identifier(payload['event_id'])
    identifier(payload['sop_id'])
    v = version(payload['version'])
    reason = bounded_text(payload['reason'], 500)

    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = setup.authorize(store, con, principal, 'setup')
        setup.available(store, con)
        history = rows(con)
        published = next((r for r in history if r['payload']['sop_id'] == payload['sop_id'] and r['payload']['version'] == v and r['payload'].get('operation') == 'PUBLISH'), None)
        if not published:
            raise ValueError('Only existing published SOP versions can be withdrawn.')
        received_at = utc_text(utc_now())
        p = {
            'event_id': payload['event_id'],
            'operation': 'WITHDRAW',
            'sop_id': payload['sop_id'],
            'version': v,
            'reason': reason,
        }
        record = {'version': 1, 'payload': p, 'actor_id': principal.id,
                  'session_id': principal.session_id, 'received_at': received_at}
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",
                    (KIND, json.dumps(record, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_SOP_WITHDRAWN', 'farming', payload['event_id'], 'RECORDED')
    return {'status': 'RECORDED', 'sop_ref': reference(payload['sop_id'], v), 'updated_at': received_at}


def delete_sop(store, principal, payload):
    """Owner-only permanent deletion control with historical audit tombstone preservation.
    
    Clearly explains that offline disconnected devices and existing external backups
    cannot be retroactively scrubbed.
    """
    if not isinstance(payload, dict):
        raise ValueError('Supply deletion payload.')
    required = {'event_id', 'sop_id', 'version', 'confirm_permanent_deletion'}
    if not required.issubset(set(payload)) or not payload['confirm_permanent_deletion']:
        raise ValueError('Confirm permanent deletion and supply event_id, sop_id, and version.')
    identifier(payload['event_id'])
    identifier(payload['sop_id'])
    v = version(payload['version'])

    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        # Permanent deletion is strictly restricted to Owner
        principal = setup.authorize(store, con, principal, 'approve')
        current_role = setup.role(con, principal)
        if current_role != 'OWNER':
            raise PermissionError('Permanent SOP deletion is reserved strictly for the Farm Owner.')
        setup.available(store, con)
        history = rows(con)
        published = next((r for r in history if r['payload']['sop_id'] == payload['sop_id'] and r['payload']['version'] == v), None)
        if not published:
            raise ValueError('Specified SOP version does not exist.')
        received_at = utc_text(utc_now())
        p = {
            'event_id': payload['event_id'],
            'operation': 'DELETE',
            'sop_id': payload['sop_id'],
            'version': v,
            'title': published['payload'].get('title', 'Deleted SOP'),
        }
        record = {'version': 1, 'payload': p, 'actor_id': principal.id,
                  'session_id': principal.session_id, 'received_at': received_at}
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",
                    (KIND, json.dumps(record, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_SOP_DELETED', 'farming', payload['event_id'], 'RECORDED')

    return {
        'status': 'DELETED',
        'sop_ref': reference(payload['sop_id'], v),
        'notice': 'The procedure was marked deleted with a historical tombstone. Historical task references preserve their execution logs. Offline copies or previous external backup archives cannot be retroactively purged.'
    }


def draft_save(store, principal, payload):
    """Create or update a draft procedure for management review."""
    if not isinstance(payload, dict):
        raise ValueError('Supply draft payload.')
    required = {'draft_id', 'sop_id', 'version', 'title', 'purpose', 'steps'}
    if not required.issubset(set(payload)):
        raise ValueError('Supply draft_id, sop_id, version, title, purpose, and steps.')
    d = dict(payload)
    identifier(d['draft_id']); identifier(d['sop_id']); d['version'] = version(d['version'])
    d['title'] = bounded_text(d['title'], 160)
    d['purpose'] = bounded_text(d['purpose'], 1000)
    d['steps'] = _text_list(d['steps'], limit=40, item_limit=1000, name='Draft steps')
    d['warnings'] = [bounded_text(w, 800) for w in d.get('warnings', [])] if isinstance(d.get('warnings'), list) else []
    d['framework_mappings'] = [bounded_text(m, 50) for m in d.get('framework_mappings', [])] if isinstance(d.get('framework_mappings'), list) else []
    d['review_status'] = 'PENDING_REVIEW'
    d['operation'] = 'DRAFT'

    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = setup.authorize(store, con, principal, 'setup')
        setup.available(store, con)
        received_at = utc_text(utc_now())
        record = {'version': 1, 'payload': d, 'actor_id': principal.id,
                  'session_id': principal.session_id, 'received_at': received_at}
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",
                    (DRAFT_KIND, json.dumps(record, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_SOP_DRAFT_SAVED', 'farming', d['draft_id'], 'RECORDED')
    return {'status': 'RECORDED', 'draft_id': d['draft_id'], 'sop_ref': reference(d['sop_id'], d['version']), 'updated_at': received_at}


def draft_review(store, principal, payload):
    """Owner/Manager reviews a draft procedure and records approval/rejection."""
    if not isinstance(payload, dict):
        raise ValueError('Supply draft review payload.')
    required = {'draft_id', 'decision', 'reviewer_notes'}
    if not required.issubset(set(payload)):
        raise ValueError('Supply draft_id, decision, and reviewer_notes.')
    decision = payload['decision']
    if decision not in {'APPROVED', 'REJECTED', 'NEEDS_REVISION'}:
        raise ValueError('Decision must be APPROVED, REJECTED, or NEEDS_REVISION.')
    notes = bounded_text(payload['reviewer_notes'], 1000)
    identifier(payload['draft_id'])

    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = setup.authorize(store, con, principal, 'setup')
        setup.available(store, con)
        drafts = draft_rows(con)
        target = next((d for d in drafts if d['payload']['draft_id'] == payload['draft_id']), None)
        if not target:
            raise ValueError('Draft not found.')
        p = dict(target['payload'])
        p['review_status'] = decision
        p['reviewer_notes'] = notes
        p['reviewed_by'] = principal.id
        p['reviewed_at'] = utc_text(utc_now())
        record = {'version': 1, 'payload': p, 'actor_id': principal.id,
                  'session_id': principal.session_id, 'received_at': p['reviewed_at']}
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",
                    (DRAFT_KIND, json.dumps(record, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_SOP_DRAFT_REVIEWED', 'farming', payload['draft_id'], 'RECORDED')
    return {'status': 'RECORDED', 'draft_id': payload['draft_id'], 'decision': decision, 'updated_at': p['reviewed_at']}


def draft_list(store, principal):
    """List all procedure drafts with their review status."""
    with store._connect() as con:
        principal = setup.authorize(store, con, principal, 'setup')
        rows_data = draft_rows(con)
    latest_drafts = {}
    for r in rows_data:
        p = r['payload']
        latest_drafts[p['draft_id']] = {
            'draft_id': p['draft_id'],
            'sop_id': p['sop_id'],
            'version': p['version'],
            'title': p['title'],
            'purpose': p['purpose'],
            'steps': p['steps'],
            'warnings': p.get('warnings', []),
            'review_status': p.get('review_status', 'PENDING_REVIEW'),
            'reviewer_notes': p.get('reviewer_notes'),
            'reviewed_by': p.get('reviewed_by'),
            'reviewed_at': p.get('reviewed_at'),
            'framework_mappings': p.get('framework_mappings', []),
            'updated_at': r['received_at'],
        }
    items = sorted(latest_drafts.values(), key=lambda x: (x['sop_id'], x['version']))
    return {'drafts': items, 'total': len(items)}


def upload_attachment(store, principal, sop_id, sop_version, filename, content_b64):
    """Safely validate and persist an untrusted PDF or document attachment for an SOP."""
    from control.files import validate_document
    identifier(sop_id)
    v = version(sop_version)
    suffix, data = validate_document(filename, content_b64)
    if suffix not in {'.pdf', '.docx', '.txt'}:
        raise ValueError('Supported attachment formats are PDF, DOCX and TXT.')

    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = setup.authorize(store, con, principal, 'setup')
        setup.available(store, con)

        # Store in private attachments directory under store parent
        storage_dir = store.path.resolve().parent / 'sop-attachments'
        storage_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        file_hash = hashlib.sha256(data).hexdigest()
        att_id = 'sop-att-' + file_hash[:16]
        target_path = storage_dir / (att_id + suffix)

        if not target_path.exists():
            with open(target_path, 'wb') as f:
                f.write(data)

        att_meta = {
            'attachment_id': att_id,
            'sop_id': sop_id,
            'version': v,
            'filename': Path(filename).name[:100],
            'file_size': len(data),
            'sha256': file_hash,
            'mime_type': 'application/pdf' if suffix == '.pdf' else ('text/plain' if suffix == '.txt' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
            'uploaded_by': principal.id,
            'uploaded_at': utc_text(utc_now()),
        }
        record = {'version': 1, 'payload': att_meta, 'actor_id': principal.id,
                  'session_id': principal.session_id, 'received_at': att_meta['uploaded_at']}
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming',?,?,?)",
                    (ATTACHMENT_KIND, json.dumps(record, sort_keys=True), time.time()))

    return {'status': 'UPLOADED', 'attachment': att_meta}


def get_attachment(store, principal, attachment_id):
    """Retrieve validated private attachment bytes and metadata."""
    with store._connect() as con:
        setup.authorize(store, con, principal, 'read')
        rows = con.execute("SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id DESC", (ATTACHMENT_KIND,)).fetchall()
    record = None
    for r in rows:
        val = json.loads(r[0])
        if val['payload']['attachment_id'] == attachment_id:
            record = val['payload']
            break
    if not record:
        raise FileNotFoundError('Attachment record not found.')

    storage_dir = store.path.resolve().parent / 'sop-attachments'
    found = list(storage_dir.glob(f"{attachment_id}.*"))
    if not found or not found[0].is_file():
        raise FileNotFoundError('Attachment content missing from private storage.')
    path = found[0]
    return path.read_bytes(), record['mime_type'], record['filename']


def seed_starter_drafts(store, principal):
    """Seed reviewed starter drafts for standard commercial poultry operations."""
    starters = [
        {
            'draft_id': 'starter-poultry-water-01',
            'sop_id': 'poultry-water-system',
            'version': '1.0.0',
            'title': 'Daily Poultry Water-Line Inspection and Sanitization',
            'purpose': 'Ensure continuous, safe, potable water access for flocks and prevent bacterial biofilm in header tanks and nipple lines.',
            'steps': [
                'Inspect header tank level and verify water pressure at line end gauges.',
                'Walk each nipple line, triggering nipples to confirm adequate flow and unblock stuck valves.',
                'Record daily water meter volume and compare against flock age standard.',
                'Check disinfectant doser concentration (target 2-3 ppm free chlorine equivalent at drinker end).',
                'Flush line end-valves weekly or immediately if sediment or turbidity appears.',
            ],
            'warnings': [
                'Never shut water lines off during daylight or warm hours without supervisor coordination.',
                'Wear chemical splash goggles and gloves when measuring concentrated sanitizing solution.',
            ],
            'framework_mappings': ['FMARD-GAP-01', 'CODEX-CAC-RCP-15'],
        },
        {
            'draft_id': 'starter-poultry-lighting-01',
            'sop_id': 'poultry-lighting-ventilation',
            'version': '1.0.0',
            'title': 'Poultry House Lighting Schedule and Minimum Ventilation',
            'purpose': 'Maintain optimal circadian lighting and continuous air exchange to evacuate ammonia and moisture.',
            'steps': [
                'Verify automated timer switch aligns with current flock age lighting program (e.g. 16h light, 8h dark).',
                'Inspect all light bulbs; replace burnt units with matching color temperature LEDs.',
                'Measure house ammonia level; if noticeable eye/throat sensation occurs (>20 ppm), increase fan run-time immediately.',
                'Clean air inlet baffles and remove dust buildup from fan protective louvers.',
                'Verify emergency generator auto-transfer switch is armed in case of grid power loss.',
            ],
            'warnings': [
                'Do not alter lighting cycle hours abruptly without veterinary/production manager authorization.',
                'Disconnect electrical lockouts before cleaning exhaust fan blades.',
            ],
            'framework_mappings': ['CODEX-CAC-RCP-15', 'FMARD-GAP-01'],
        },
        {
            'draft_id': 'starter-poultry-biosecurity-01',
            'sop_id': 'poultry-biosecurity-barrier',
            'version': '1.0.0',
            'title': 'Farm Perimeter and Pen Entry Biosecurity Protocol',
            'purpose': 'Prevent introduction of Newcastle Disease, Highly Pathogenic Avian Influenza, and infectious pathogens.',
            'steps': [
                'Ensure all farm personnel and authorized visitors sign the visitor log before entry.',
                'Step into vehicle tire wash barrier when entering premises; spray undercarriage.',
                'Change out street clothing and shoes into dedicated farm coveralls and sanitizable rubber boots.',
                'Immerse boots in freshly mixed footbath (minimum 10 seconds) before crossing pen thresholds.',
                'Wash hands with disinfectant soap at wash station before handling feed, eggs or birds.',
                'Replenish footbath disinfectant daily or whenever organic debris obscures solution transparency.',
            ],
            'warnings': [
                'Strictly prohibit entry of external poultry products, egg cartons, or birds from other premises.',
                'Report any wild bird carcasses or sightings near pens immediately to supervisor.',
            ],
            'framework_mappings': ['WOAH-TAHC-CHAP6.5', 'FMARD-GAP-01'],
        },
        {
            'draft_id': 'starter-poultry-veterinary-01',
            'sop_id': 'poultry-veterinary-escalation',
            'version': '1.0.0',
            'title': 'Flock Health Incident and Veterinary Escalation Protocol',
            'purpose': 'Establish rapid notification triggers when daily flock mortality or clinical distress exceeds baseline.',
            'steps': [
                'Count and record daily flock mortality and culled birds at morning inspection.',
                'Calculate mortality percentage: (daily dead / starting pen count) * 100.',
                'If daily mortality exceeds 0.5% (5 birds per 1,000) or 3x previous 3-day average, trigger immediate escalation.',
                'Notify Farm Manager and assigned consulting veterinarian within 1 hour.',
                'Segregate affected pen: halt all bird and equipment movement from that house.',
                'Bag deceased birds in double-layered leakproof bags and place in secure quarantine cold-storage for post-mortem.',
            ],
            'warnings': [
                'Do not dispose of suspected infectious carcasses via open dump; use incinerator or deep covered burial pit only.',
                'Do not self-prescribe broad-spectrum antibiotics without veterinary diagnostic approval.',
            ],
            'framework_mappings': ['WOAH-TAHC-CHAP6.5', 'FMARD-GAP-02'],
        },
        {
            'draft_id': 'starter-poultry-medication-01',
            'sop_id': 'poultry-medication-tracking',
            'version': '1.0.0',
            'title': 'Veterinary Medication Administration and Withdrawal Logging',
            'purpose': 'Ensure precise pharmaceutical dosing, compliance with prescription, and observance of statutory withdrawal windows.',
            'steps': [
                'Verify veterinary prescription details: drug name, concentration, batch number, expiry date, and indicated dosage.',
                'Calculate total volume required based on current pen water consumption.',
                'Log administration in Chief Health Records: include flock ID, drug name, dosage, and administering staff name.',
                'Set explicit flock withdrawal quarantine date based on manufacturer label (e.g. 7 days post-treatment).',
                'Affix bright warning placard at pen entrance: "WITHDRAWAL RESTRICTION ACTIVE - NO SALE OF EGGS/BIRDS".',
                'Verify zero egg/meat sales dispatch until withdrawal end date passes.',
            ],
            'warnings': [
                'Never harvest eggs or market broilers during active withdrawal period; violates NAFDAC food safety regulations.',
                'Store all pharmaceuticals in locked, temperature-monitored cabinet away from feed supplies.',
            ],
            'framework_mappings': ['NAFDAC-VMD-2019', 'NIST-CSF-DATA'],
        },
    ]

    results = []
    for s in starters:
        res = draft_save(store, principal, s)
        results.append(res)
    return {'status': 'SEEDED', 'count': len(results), 'items': results}


def library(store, principal):
    """General Procedure Library accessible by all authorized Farm workers and managers.
    
    Shows both active and withdrawn published procedures, versions, steps,
    warnings, framework mappings, and attachments.
    """
    with store._connect() as con:
        setup.authorize(store, con, principal, 'read')
        cat = catalog(rows(con))

    items = list(cat.values())
    # Exclude permanently deleted items from standard staff library view (or label them)
    visible = [item for item in items if item.get('status') != 'DELETED']
    visible.sort(key=lambda item: (item.get('status') == 'ACTIVE', item['title'].lower(), item['sop_id'], item['version']), reverse=True)

    return {
        'library': visible,
        'total': len(visible),
        'active_count': sum(1 for i in visible if i.get('status') == 'ACTIVE'),
        'withdrawn_count': sum(1 for i in visible if i.get('status') == 'WITHDRAWN'),
        'notice': 'General published Farm procedure library. Always follow the specific version pinned to your assigned daily task.'
    }


def overview(store, principal):
    """Management catalog. The Staff application uses ``assigned`` or ``library``."""
    with store._connect() as con:
        principal = setup.authorize(store, con, principal, 'setup')
        items = list(catalog(rows(con)).values())
    items.sort(key=lambda item: (item['sop_id'], item['version']))
    return {'sops': items, 'total': len(items),
            'notice': 'Published SOP versions are immutable. Assign tasks using an exact sop_id@version reference.'}


def assigned(store, principal):
    # Ask the authoritative task projection what this principal can see; this
    # preserves Worker/Supervisor/Manager organizational scoping in one place.
    task_view = tasks.overview(store, principal)
    refs = []
    invalid_refs = []
    for task in task_view['tasks']:
        value = task.get('sop_ref')
        if value is None:
            continue
        try:
            pinned = parse_reference(value)
        except ValueError:
            invalid_refs.append(value)
            continue
        if pinned not in refs:
            refs.append(pinned)

    with store._connect() as con:
        setup.authorize(store, con, principal, 'read')
        available = catalog(rows(con))
    result = []
    unavailable = list(invalid_refs)
    for key in refs:
        item = available.get(key)
        if item is None or item.get('status') == 'DELETED':
            unavailable.append(reference(*key))
        else:
            proj = {
                'sop_id': item['sop_id'],
                'version': item['version'],
                'title': item['title'],
                'purpose': item['purpose'],
                'steps': list(item['steps']),
                'warnings': list(item['warnings']),
                'updated_at': item['updated_at'],
            }
            if proj not in result:
                result.append(proj)
    result.sort(key=lambda item: (item['title'].lower(), item['sop_id'], item['version']))
    notice = 'Only exact SOP versions referenced by tasks in your Chief-authorized scope are returned.'
    if unavailable:
        notice += ' Some task SOP references are unavailable or unversioned and were not substituted.'
    return {'sops': result, 'total': len(result), 'notice': notice}
