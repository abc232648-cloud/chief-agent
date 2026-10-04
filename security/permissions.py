from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerContext:
    agent_id: str
    domain: str
    capabilities: frozenset[str]
    allowed: object = None
    component_allowed: object = None

    def require(self, capability):
        if self.allowed is not None and not self.allowed(self.domain):
            raise PermissionError('Agent is paused or disabled; no new operation may begin.')
        if capability not in self.capabilities:
            raise PermissionError(f'Agent {self.agent_id} has no {capability} permission.')
        if self.component_allowed is not None and not self.component_allowed(self.agent_id,capability):
            raise PermissionError('Component mode prevents a new guarded operation.')


_current = ContextVar('chief_agent_context', default=None)


def current_context():
    return _current.get()


def require_if_scoped(capability):
    context = current_context()
    if context is not None:
        context.require(capability)


@contextmanager
def worker_context(agent, allowed=None, component_allowed=None):
    context = WorkerContext(agent.id, agent.domain, agent.capabilities, allowed, component_allowed)
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)
