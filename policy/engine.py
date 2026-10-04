"""Pure deterministic resolution. No actions executed and no authority promoted."""
from .contracts import ActionRisk, Outcome, PolicyPack, PolicyResult


class PolicyEngine:
    def __init__(self, packs):
        self.packs = tuple(sorted(packs, key=lambda p: (p.precedence, p.id)))
        if any(not isinstance(p, PolicyPack) for p in self.packs) or len({p.id for p in self.packs}) != len(self.packs):
            raise ValueError('Pin exactly one version per policy pack.')

    def evaluate(self, request):
        if not isinstance(request.risk, ActionRisk):
            raise ValueError('A formal risk class is required.')
        matches = [(p, r) for p in self.packs for r in p.rules if r.action in (request.action, '*')]
        if not matches:
            return PolicyResult(Outcome.ASK, request.risk, 'Unknown action requires user approval', (), (), False)
        hard = [(p, r) for p, r in matches if r.hard_prohibition]
        level = min(p.precedence for p, _ in matches)
        governing = hard or [(p, r) for p, r in matches if p.precedence == level]
        strength = {Outcome.ALLOW: 0, Outcome.ASK: 1, Outcome.BLOCK: 2}
        chosen = max(governing, key=lambda pair: strength[pair[1].decision])[1]
        return PolicyResult(chosen.decision, request.risk, chosen.reason,
                            tuple((p.id, p.version) for p, _ in matches),
                            tuple((p.id, p.version) for p, _ in governing),
                            len({r.decision for _, r in matches}) > 1)
