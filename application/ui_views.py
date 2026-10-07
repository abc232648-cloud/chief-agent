"""Read-only projections of existing foundation state, with explicit maturity limits."""
from dataclasses import asdict
import json
from pathlib import Path
from .composition import default_catalogs

def system_view(store,page):
    if page=='capabilities':return default_catalogs().capabilities.describe()
    if page=='models':
        import os
        from model_registry.setup import ModelSetup
        from .auth_routes import model_registry
        from model_registry.contracts import Assignment
        registry=model_registry(store)
        assignment=registry.assignment(Assignment('jobs','jobs-worker',('jobs.qwen','jobs.mistral'),'JOB_LEGACY_COMPATIBILITY'))
        from gateway.availability import MESSAGES
        observations=[]
        with store._connect() as con:
            for provider in ('Groq','Mistral','Farm Groq'):
                row=con.execute('SELECT body,created_at FROM notifications WHERE title=? ORDER BY id DESC LIMIT 1',(provider+' model connection',)).fetchone()
                code=row['body'].split(':',1)[0] if row else None
                observations.append({'provider':provider,'status':code if code in MESSAGES else 'NOT_OBSERVED',
                                     'message':MESSAGES.get(code,'No worker observation recorded.'),
                                     'observed_at':row['created_at'] if row and code in MESSAGES else None})
        return {'provider_observations':observations,'registrations':ModelSetup(Path(os.environ['CHIEF_STATE_ROOT'])).list(),
                'models':[dict(asdict(m),state=registry.state(m.id).value) for m in registry.models.values()],
                'job_assignment':asdict(assignment),'eligible_route_models':[m for m in assignment.models if registry.eligible(m,assignment)],
                'policy':asdict(registry.policy()),'routing':'Qwen/Groq primary → Mistral fallback; existing Job compatibility route.',
                'limitation':'Mistral/free-only discrepancy remains OPEN. State is not provider qualification; shadow output has no live authority.'}
    if page=='policies':
        from policy.contracts import Precedence,ActionRisk
        return {'precedence':[p.name for p in Precedence],'risk_classes':[r.value for r in ActionRisk],
                'enforcement':'Existing Job PolicyGate remains authoritative. New Chief policy engine is comparison-only for Job.',
                'editing':'Policy editing is unavailable in this UI.'}
    if page=='runtime':
        from runtime_qualification.catalog import load_candidates
        from runtime_qualification.evaluator import evaluate_candidate
        candidates=load_candidates((Path(__file__).resolve().parents[1]/'config/runtime_candidates.json').read_text())
        return {'decision':'NO_QUALIFIED_CANDIDATE_YET','selected':None,'installed_by_chief':False,
                'candidates':[{'id':c.id,'product':c.product,'version':c.version,'qualification':asdict(evaluate_candidate(c,()))} for c in candidates],
                'limitation':'Pinned research candidates only. No executed qualification evidence is loaded here; documentation does not prove a hard gate.'}
    if page=='updates':
        from update_center.history import read
        return {'status':'MANUAL_HISTORY_ONLY','activation_available':False,'history':read(store),
                'limitation':'Operator-recorded activity, not an independent live release or compatibility check. Automatic installation and activation remain unavailable.'}
    if page=='devices':return {'status':'UNAVAILABLE','limitation':'Device management and Farm hardware workflows are not implemented. No device discovery or control is performed.'}
    if page=='integrations':
        from integrations.n8n import describe
        return {'status':'LIMITED','n8n':describe(store), 'limitation':'n8n connection and metadata only. Existing Chief workflows remain unchanged.'}
    if page in {'components','settings'}:return {'status':'USE_EXISTING_AUTHORIZED_API'}
    raise LookupError('Unknown system surface.')

def domain_view(store,page,domain):
    """Bounded metadata only. WHERE domain is applied before serialization."""
    from database.evidence_migrations import schema_ready as evidence_ready
    from database.execution_migrations import schema_ready as execution_ready
    with store._connect() as con:
        con.execute('BEGIN')
        if not (execution_ready(con) if page=='runbooks' else evidence_ready(con)):
            return {'domain':domain,'status':'UNAVAILABLE','rows':[],'limitation':'Required foundation schema is unavailable; this view never migrates it.'}
        if page=='runbooks':
            rows=[dict(r) for r in con.execute('SELECT id,definition_id,version,digest,state,step,revision FROM sop_runs WHERE domain=? ORDER BY id LIMIT 100',(domain,))]
            return {'domain':domain,'status':'READ_ONLY','rows':rows,'limit':100,'limitation':'Version-pinned run references only. No run/start/resume action; mature Job pipeline remains Job-owned.'}
        if page=='ledger':
            rows=[]
            for row in con.execute('SELECT id,correlation_id,record FROM decision_ledger WHERE domain=? ORDER BY id DESC LIMIT 100',(domain,)):
                record=json.loads(row['record'])
                fields=('capability','actor','action','phase','outcome','risk','policy_decision','confidence','approval','received_at','policy_ref','evidence_ids')
                rows.append(dict(id=row['id'],correlation_id=row['correlation_id'],**{k:record.get(k) for k in fields}))
            return {'domain':domain,'status':'READ_ONLY','rows':rows,'limit':100,'limitation':'Reference-only Decision Ledger view. Existing Job audit, application events and snapshots remain authoritative history.'}
        rows=[]
        for row in con.execute('SELECT id,record FROM shared_evidence WHERE domain=? ORDER BY id LIMIT 100',(domain,)):
            record=json.loads(row['record']);identity=row['id']
            latest=con.execute('SELECT state FROM evidence_verifications WHERE domain=? AND evidence_id=? ORDER BY id DESC LIMIT 1',(domain,identity)).fetchone()
            quality=con.execute('SELECT result FROM evidence_quality_assessments WHERE domain=? AND evidence_id=? ORDER BY id DESC LIMIT 1',(domain,identity)).fetchone()
            conflicts=[dict(r) for r in con.execute('SELECT c.conflict_id,c.state FROM evidence_contradictions c WHERE c.domain=? AND (c.left_id=? OR c.right_id=?) AND c.id=(SELECT MAX(x.id) FROM evidence_contradictions x WHERE x.domain=c.domain AND x.conflict_id=c.conflict_id)',(domain,identity,identity))]
            rows.append({'id':identity,'truth':record.get('truth'),'verification':latest[0] if latest else record.get('verification'),
                         'source_owner':record.get('source_owner'),'source_kind':record.get('source_kind'),'source_ref':record.get('source_ref'),
                         'confidence':record.get('confidence'),'time':record.get('time'),'contradictions':conflicts,
                         'last_recorded_quality':json.loads(quality[0]) if quality else None})
        return {'domain':domain,'status':'READ_ONLY','rows':rows,'limit':100,'limitation':'Recorded metadata only; quality may be stale. Truth, verification, confidence, sufficiency and action authority are separate. Generic VERIFIED never substitutes for Job USER_CONFIRMED; no claim authority is granted here.'}
