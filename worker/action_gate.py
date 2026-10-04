from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any

from policy.rules import Decision, PolicyDecision, evaluate_action


class PolicyBlocked(RuntimeError):
    pass


class ApprovalRequired(RuntimeError):
    pass


@dataclass(frozen=True)
class ActionRequest:
    action: str
    payload: dict[str, Any]


class PolicyGate:
    """Mandatory policy boundary between the worker and computer/browser actions."""

    def decide(self, request: ActionRequest) -> PolicyDecision:
        from security.permissions import current_context
        context = current_context()
        if context is not None:
            try:context.require('jobs.execute')
            except PermissionError as exc:return PolicyDecision(Decision.BLOCK, str(exc))
        if context is not None and (context.domain != 'jobs' or 'jobs.execute' not in context.capabilities):
            return PolicyDecision(Decision.BLOCK, 'This agent cannot execute job actions.')
        return evaluate_action(request.action)

    def execute(
        self,
        request: ActionRequest,
        executor: Callable[[dict[str, Any]], Any],
        *,
        approved: bool = False,
    ) -> Any:
        decision = self.decide(request)

        if decision.decision == Decision.BLOCK:
            raise PolicyBlocked(f"BLOCK: {decision.reason}")

        if decision.decision == Decision.ASK and not approved:
            raise ApprovalRequired(f"ASK: {decision.reason}")

        return executor(request.payload)
