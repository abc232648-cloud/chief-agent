"""Domain-neutral policy contracts; risk never grants permission by itself."""
from dataclasses import dataclass
from enum import Enum, IntEnum
from capabilities.contracts import version


class ActionRisk(str, Enum):
    READ = 'READ'
    RECORD = 'RECORD'
    ADVISE = 'ADVISE'
    LOW_RISK_AUTOMATION = 'LOW_RISK_AUTOMATION'
    PHYSICAL_FINANCIAL = 'PHYSICAL_FINANCIAL'
    HIGH_IMPACT = 'HIGH_IMPACT'


class Outcome(str, Enum):
    ALLOW = 'ALLOW'
    ASK = 'ASK'
    BLOCK = 'BLOCK'


class Precedence(IntEnum):
    LAW_REGULATION = 0
    HARD_SAFETY_SECURITY = 1
    PROFESSIONAL_FRAMEWORK = 2
    CHIEF = 3
    INSTALLATION_OWNER = 4
    AGENT_PREFERENCE = 5
    MODEL_RECOMMENDATION = 6


@dataclass(frozen=True)
class PolicyRule:
    action: str
    decision: Outcome
    reason: str
    hard_prohibition: bool = False

    def __post_init__(self):
        if not self.action or not self.reason or not isinstance(self.decision, Outcome):
            raise ValueError('Invalid policy rule.')
        if type(self.hard_prohibition) is not bool or (self.hard_prohibition and self.decision != Outcome.BLOCK):
            raise ValueError('Hard prohibitions must BLOCK.')


@dataclass(frozen=True)
class PolicyPack:
    id: str
    version: str
    precedence: Precedence
    rules: tuple[PolicyRule, ...]

    def __post_init__(self):
        version(self.version)
        if not self.id or not isinstance(self.precedence, Precedence) or not isinstance(self.rules, tuple):
            raise ValueError('Invalid versioned policy pack.')
        if any(not isinstance(r, PolicyRule) for r in self.rules) or len({r.action for r in self.rules}) != len(self.rules):
            raise ValueError('Duplicate/invalid rule in policy pack.')


@dataclass(frozen=True)
class PolicyRequest:
    action: str
    risk: ActionRisk


@dataclass(frozen=True)
class PolicyResult:
    decision: Outcome
    risk: ActionRisk
    reason: str
    matched_packs: tuple[tuple[str, str], ...]
    governing_packs: tuple[tuple[str, str], ...]
    conflict: bool
