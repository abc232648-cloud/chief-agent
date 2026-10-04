"""Offline text comparison only: no provider, planner, executor or live response."""
from dataclasses import dataclass,asdict
import hashlib
import uuid
from evidence.service import canonical
from evidence.contracts import token
from decision_ledger.service import DecisionLedger
from operations.time_integrity import utc_now,utc_text
from security.permissions import current_context


@dataclass(frozen=True)
class ShadowMetrics:
    id: str
    same_text: bool
    reference_chars: int
    shadow_chars: int
    reference_sha256: str
    shadow_sha256: str


def evaluate_shadow(registry,assignment,model_id,reference_text,shadow_text,*,capability,correlation):
    context=current_context()
    if context is None or (context.domain,context.agent_id)!=(assignment.domain,assignment.agent):
        raise PermissionError('Shadow results belong to the active domain/agent.')
    context.require(capability)
    if not registry.eligible(model_id,assignment,shadow=True):raise PermissionError('Shadow model is disabled or blocked by installation policy.')
    if not isinstance(reference_text,str) or not isinstance(shadow_text,str):raise TypeError('Prepared text only; no callbacks or action objects.')
    identity=uuid.uuid4().hex
    digest=lambda s:hashlib.sha256(s.encode()).hexdigest()
    metrics=ShadowMetrics(identity,reference_text==shadow_text,len(reference_text),len(shadow_text),digest(reference_text),digest(shadow_text))
    registry._write('INSERT INTO shadow_evaluations VALUES(?,?,?,?,?)',(identity,assignment.domain,model_id,canonical(asdict(metrics)),utc_text(utc_now())))
    def reference_valid(ref):
        return ref=={'domain':assignment.domain,'kind':'shadow_evaluation','id':identity}
    ledger=DecisionLedger(registry.store,assignment.domain,capability,reference_validator=reference_valid)
    ledger.append(event_key=identity,correlation_id=token(correlation),action='shadow_evaluation',phase='OUTCOME',
                  outcome='MATCH' if metrics.same_text else 'DIFFERENT',rationale='TEXT_COMPARISON_V1',risk='READ',policy_decision='NOT_EVALUATED',
                  references=[{'domain':assignment.domain,'kind':'shadow_evaluation','id':identity}])
    return metrics
