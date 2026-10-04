from dataclasses import dataclass
from enum import Enum
from capabilities.contracts import version
from evidence.contracts import token


class ModelState(str, Enum):
    ENABLED = 'ENABLED'
    SHADOW = 'SHADOW'
    DISABLED = 'DISABLED'


class Cost(str, Enum):
    FREE = 'FREE'
    PAID = 'PAID'
    UNKNOWN = 'UNKNOWN'
    LEGACY_UNRESOLVED = 'LEGACY_UNRESOLVED'


@dataclass(frozen=True)
class Provider:
    id: str
    transport_version: str = '1.0.0'

    def __post_init__(self):
        token(self.id); version(self.transport_version)


@dataclass(frozen=True)
class Model:
    id: str
    provider: str
    provider_model: str
    cost: Cost
    version: str = '1.0.0'

    def __post_init__(self):
        token(self.id);token(self.provider);version(self.version)
        if not isinstance(self.provider_model,str) or not self.provider_model.strip() or len(self.provider_model)>200:
            raise ValueError('A bounded configured provider model is required.')
        object.__setattr__(self,'cost',Cost(self.cost))


@dataclass(frozen=True)
class Assignment:
    domain: str
    agent: str
    models: tuple[str, ...]
    profile: str = 'STRICT_FREE'

    def __post_init__(self):
        token(self.domain);token(self.agent)
        if self.profile not in {'STRICT_FREE','JOB_LEGACY_COMPATIBILITY'}:
            raise ValueError('Unknown routing profile.')
        if not isinstance(self.models,tuple) or not self.models or len(set(self.models))!=len(self.models):
            raise ValueError('An ordered unique immutable model list is required.')
        for model in self.models:token(model)
        if self.profile=='JOB_LEGACY_COMPATIBILITY' and (self.domain,self.agent)!=('jobs','jobs-worker'):
            raise ValueError('Legacy exception belongs only to the existing Job worker.')


@dataclass(frozen=True)
class InstallationPolicy:
    allowed_models: tuple[str, ...]
    free_only: bool = True
    legacy_job_compatibility: bool = True
    version: str = '1.0.0'

    def __post_init__(self):
        version(self.version)
        if self.free_only is not True or type(self.legacy_job_compatibility) is not bool:
            raise ValueError('E cannot enable paid routing.')
        if not isinstance(self.allowed_models,tuple) or len(set(self.allowed_models))!=len(self.allowed_models):
            raise ValueError('An immutable unique installation allowlist is required.')
        for identity in self.allowed_models:token(identity)
