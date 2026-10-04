from dataclasses import asdict
import json
from types import MappingProxyType
from evidence.service import canonical
from evidence.contracts import token
from operations.time_integrity import utc_now,utc_text
from security.permissions import current_context
from database.execution_migrations import schema_ready
from .contracts import Assignment,InstallationPolicy,ModelState,Cost


class ModelRegistry:
    """Trusted installation configuration API; no remote/admin endpoint in E."""
    def __init__(self, providers, models, *, store=None, policy=None, legacy_pair=()):
        providers,models=tuple(providers),tuple(models)
        if len({p.id for p in providers})!=len(providers) or len({m.id for m in models})!=len(models):
            raise ValueError('Duplicate provider/model identity.')
        if any(m.provider not in {p.id for p in providers} for m in models):
            raise ValueError('Unknown model provider.')
        self.providers=MappingProxyType({p.id:p for p in providers})
        self.models=MappingProxyType({m.id:m for m in models})
        self.store=store
        self.default_policy=policy or InstallationPolicy(tuple(self.models))
        self.legacy_pair=tuple(legacy_pair)
        self._validate_policy(self.default_policy)
        if self.legacy_pair and (len(self.legacy_pair)!=2 or any(i not in self.models for i in self.legacy_pair)):
            raise ValueError('Legacy compatibility requires the configured primary/fallback pair.')

    def _validate_policy(self,policy):
        if not isinstance(policy,InstallationPolicy) or not set(policy.allowed_models)<=self.models.keys():
            raise ValueError('Unknown installation model.')

    def _ready(self,con):
        return schema_ready(con)

    def state(self,identity):
        if identity not in self.models:raise LookupError('Unknown model.')
        if self.store is not None:
            with self.store._connect() as con:
                if self._ready(con):
                    row=con.execute('SELECT state FROM model_states WHERE model_id=?',(identity,)).fetchone()
                    if row:return ModelState(row[0])
        return ModelState.ENABLED

    def policy(self):
        if self.store is not None:
            with self.store._connect() as con:
                if self._ready(con):
                    row=con.execute('SELECT record FROM installation_policies WHERE id=1').fetchone()
                    if row:
                        # The persisted installation policy may name models used
                        # by another domain. A local router sees only its subset;
                        # intersection cannot add an installation permission.
                        value=json.loads(row[0]);value['allowed_models']=tuple(m for m in value['allowed_models'] if m in self.models)
                        result=InstallationPolicy(**value);self._validate_policy(result);return result
        return self.default_policy

    def assignment(self,default):
        if self.store is not None:
            with self.store._connect() as con:
                if self._ready(con):
                    row=con.execute('SELECT record FROM model_assignments WHERE domain=? AND agent=?',(default.domain,default.agent)).fetchone()
                    if row:
                        value=json.loads(row[0]);value['models']=tuple(value['models'])
                        result=Assignment(**value)
                        if (result.domain,result.agent)!=(default.domain,default.agent):raise ValueError('Assignment identity mismatch.')
                        return result
        return default

    def _write(self,sql,args):
        if self.store is None:raise RuntimeError('Persistence requires an explicitly migrated development store.')
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if not self._ready(con):raise RuntimeError('Explicit E migration required.')
            con.execute(sql,args)

    def set_global_state(self,identity,state,*,actor):
        if identity not in self.models:raise LookupError('Unknown model.')
        self._write('INSERT INTO model_states VALUES(?,?,?,?) ON CONFLICT(model_id) DO UPDATE SET state=excluded.state,actor=excluded.actor,changed_at=excluded.changed_at',
                    (identity,ModelState(state).value,token(actor),utc_text(utc_now())))

    def set_installation_policy(self,policy,*,actor):
        self._validate_policy(policy)
        self._write('INSERT INTO installation_policies VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET record=excluded.record,actor=excluded.actor,changed_at=excluded.changed_at',
                    (canonical(asdict(policy)),token(actor),utc_text(utc_now())))

    def assign(self,assignment,*,capability):
        context=current_context()
        if context is None or (context.domain,context.agent_id)!=(assignment.domain,assignment.agent):
            raise PermissionError('Assignments are private to the active domain/agent.')
        context.require(capability)
        if not set(assignment.models)<=self.models.keys():raise LookupError('Unknown assigned model.')
        self._write('INSERT INTO model_assignments VALUES(?,?,?,?,?) ON CONFLICT(domain,agent) DO UPDATE SET record=excluded.record,actor=excluded.actor,changed_at=excluded.changed_at',
                    (assignment.domain,assignment.agent,canonical(asdict(assignment)),token(context.agent_id),utc_text(utc_now())))

    def eligible(self,identity,assignment,*,shadow=False):
        model=self.models.get(identity)
        if model is None:return False
        state=self.state(identity)
        if state!=(ModelState.SHADOW if shadow else ModelState.ENABLED):return False
        policy=self.policy()
        if identity not in policy.allowed_models:return False
        if model.cost==Cost.FREE:return True
        return (model.cost==Cost.LEGACY_UNRESOLVED and policy.legacy_job_compatibility
                and assignment.profile=='JOB_LEGACY_COMPATIBILITY'
                and (assignment.domain,assignment.agent)==('jobs','jobs-worker')
                and assignment.models==self.legacy_pair and identity in self.legacy_pair)
