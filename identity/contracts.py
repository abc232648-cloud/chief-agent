from dataclasses import dataclass
from types import MappingProxyType

DOMAIN_PERMISSIONS=frozenset({'work.read','work.request','work.manage'})
MATRIX=MappingProxyType({
    'Owner':DOMAIN_PERMISSIONS|{'work.approve','controls.manage','installation.manage','identity.manage','audit.read','delegation.manage','emergency.manage','work.delete'},
    'Administrator':DOMAIN_PERMISSIONS|{'work.approve','controls.manage','installation.manage','identity.manage','audit.read','emergency.manage','work.delete'},
    'Manager':DOMAIN_PERMISSIONS|{'identity.workers.manage'},
    'Worker':frozenset({'work.read','work.request'}),
})
SENSITIVE=frozenset({'work.approve','work.delete','controls.manage','installation.manage','identity.manage','identity.workers.manage','delegation.manage','emergency.manage'})


class AuthenticationRequired(PermissionError):pass
class ReauthenticationRequired(PermissionError):pass
class SetupRequired(PermissionError):pass


@dataclass(frozen=True)
class Principal:
    id: str
    session_id: str
    role: str
    domains: tuple[str,...]
    reauth_at: str


def roles(extra=None):
    result=dict(MATRIX)
    for name,permissions in (extra or {}).items():
        if name in result or not name or not set(permissions)<=DOMAIN_PERMISSIONS:
            raise ValueError('Domain roles cannot replace base roles or acquire global authority.')
        result[name]=frozenset(permissions)
    return MappingProxyType(result)
