from dataclasses import dataclass,asdict
import hashlib
from types import MappingProxyType
from capabilities.contracts import version
from evidence.contracts import token
from evidence.service import canonical
from policy.contracts import ActionRisk


@dataclass(frozen=True)
class Step:
    id: str
    action: str
    branches: tuple[tuple[str,str | None], ...] = (('OK',None),)
    prerequisites: tuple[str,...] = ()
    evidence_required: bool = False
    approval_required: bool = False
    failure_next: str | None = None

    def __post_init__(self):
        token(self.id);token(self.action)
        if not isinstance(self.branches,tuple) or not self.branches or len(dict(self.branches))!=len(self.branches):raise ValueError('Unique immutable branches required.')
        for outcome,target in self.branches:
            token(outcome)
            if target is not None:token(target)
        if not isinstance(self.prerequisites,tuple):raise ValueError('Immutable prerequisites required.')
        for item in self.prerequisites:token(item)
        if type(self.evidence_required) is not bool or type(self.approval_required) is not bool:raise ValueError('Boolean requirements required.')
        if self.failure_next is not None:token(self.failure_next)


@dataclass(frozen=True)
class Definition:
    domain: str
    id: str
    version: str
    start: str
    steps: tuple[Step,...]

    def __post_init__(self):
        token(self.domain);token(self.id);version(self.version);token(self.start)
        if not isinstance(self.steps,tuple) or not self.steps or any(not isinstance(s,Step) for s in self.steps):raise ValueError('Immutable validated steps required.')
        names={s.id for s in self.steps}
        if len(names)!=len(self.steps) or self.start not in names:raise ValueError('Invalid step identities/start.')
        edges={s.id:[t for _,t in s.branches if t is not None]+([s.failure_next] if s.failure_next else []) for s in self.steps}
        if any(t not in names for targets in edges.values() for t in targets):raise ValueError('Missing branch target.')
        def visit(node,path):
            if node in path:raise ValueError('Cycles are not supported in E runbooks.')
            for target in edges[node]:visit(target,path|{node})
        visit(self.start,set())
        # Validate unreachable subgraphs too; their content is still pinned.
        for node in names:visit(node,set())

    @property
    def digest(self):return hashlib.sha256(canonical(asdict(self)).encode()).hexdigest()

    @classmethod
    def from_record(cls,value):
        steps=tuple(Step(**{**s,'branches':tuple(tuple(b) for b in s['branches']),'prerequisites':tuple(s['prerequisites'])}) for s in value['steps'])
        return cls(**{**value,'steps':steps})


@dataclass(frozen=True)
class Binding:
    action: str
    capability: str
    risk: ActionRisk
    version: str
    implementation_ref: str
    handler: object
    external: bool = False

    def __post_init__(self):
        token(self.action);token(self.capability);version(self.version);token(self.implementation_ref)
        if not isinstance(self.risk,ActionRisk) or not callable(self.handler) or type(self.external) is not bool:raise ValueError('Validated domain action binding required.')

    def contract(self):
        return dict(action=self.action,capability=self.capability,risk=self.risk.value,version=self.version,implementation_ref=self.implementation_ref,external=self.external)
