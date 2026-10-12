"""Generic read-only research routing; domains never select a runtime or provider directly."""
from dataclasses import dataclass
import json
from pathlib import Path
import re


_TOKEN = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')


@dataclass(frozen=True)
class ResearchPolicy:
    interface: str
    internal_data_route: str
    external_research_route: str
    initial_search_provider: str
    enabled_search_providers: tuple[str, ...]
    reserved_search_providers: tuple[str, ...]
    automatic_provider_fallback: bool
    action_authority: str


@dataclass(frozen=True)
class ResearchRoute:
    status: str
    route: str
    runtime_candidate_id: str | None
    lifecycle: str
    action_authority: str
    search_provider: str | None


_EXPECTED = {
    'interface', 'internal_data_route', 'external_research_route',
    'initial_search_provider', 'enabled_search_providers',
    'reserved_search_providers', 'automatic_provider_fallback',
    'action_authority',
}


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate research policy key.')
        result[key] = value
    return result


def _providers(label, value):
    if not isinstance(value, list) or any(not isinstance(item, str) or not _TOKEN.fullmatch(item) for item in value):
        raise ValueError(label + ' must contain provider identifiers.')
    if len(set(value)) != len(value):
        raise ValueError(label + ' must contain unique providers.')
    return tuple(value)


def load_research_policy(data):
    if isinstance(data, Path):
        data = data.read_text(encoding='utf-8')
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data).decode('utf-8')
    if isinstance(data, str):
        data = json.loads(data, object_pairs_hook=_no_duplicates)
    if not isinstance(data, dict) or set(data) != _EXPECTED:
        raise ValueError('Research policy has an invalid shape.')
    if data['interface'] != 'PROVIDER_NEUTRAL':
        raise ValueError('Research interface must remain provider neutral.')
    if data['internal_data_route'] != 'CHIEF_INTERNAL':
        raise ValueError('Internal data must remain inside Chief.')
    if data['external_research_route'] != 'RESEARCH_RUNTIME':
        raise ValueError('External research must route through the research runtime.')
    if not isinstance(data['initial_search_provider'], str) or not _TOKEN.fullmatch(data['initial_search_provider']):
        raise ValueError('Initial search provider is invalid.')
    enabled = _providers('Enabled search providers', data['enabled_search_providers'])
    reserved = _providers('Reserved search providers', data['reserved_search_providers'])
    if not enabled or data['initial_search_provider'] not in enabled:
        raise ValueError('Initial search provider must be enabled.')
    if set(enabled) & set(reserved):
        raise ValueError('Enabled and reserved search providers must be disjoint.')
    if data['automatic_provider_fallback'] is not False:
        raise ValueError('Automatic search-provider fallback is not authorized.')
    if data['action_authority'] != 'NONE':
        raise ValueError('Research must have no action authority.')
    return ResearchPolicy(
        data['interface'], data['internal_data_route'], data['external_research_route'],
        data['initial_search_provider'], enabled, reserved,
        data['automatic_provider_fallback'], data['action_authority'],
    )


def load_default_research_policy(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    return load_research_policy(root / 'config' / 'research_policy.json')


def route_research(source_scope, runtime_plan, runtime_policy, research_policy, *, caller=None):
    """Route by information boundary, never by current agent name.

    ``caller`` is intentionally non-authoritative. It exists only so callers can
    retain their own correlation metadata without creating a runtime allowlist.
    """
    del caller
    if source_scope == 'INTERNAL':
        return ResearchRoute(
            'READY', research_policy.internal_data_route, None, 'INTERNAL',
            'NONE', None,
        )
    if source_scope != 'EXTERNAL':
        raise ValueError('Research source scope must be INTERNAL or EXTERNAL.')
    if runtime_policy.research_role != 'RESEARCH_ONLY' or runtime_policy.research_action_authority != 'NONE':
        raise PermissionError('Research runtime authority contract is invalid.')
    if runtime_policy.research_lifecycle != 'ON_DEMAND':
        raise PermissionError('Research runtime must be on demand.')
    if not runtime_policy.auto_route_external_research:
        raise PermissionError('Automatic external research routing is disabled.')
    if not runtime_plan.research_qualified or not runtime_plan.external_research_auto_routed:
        return ResearchRoute(
            'UNAVAILABLE', research_policy.external_research_route, None,
            runtime_policy.research_lifecycle, 'NONE', None,
        )
    return ResearchRoute(
        'READY', research_policy.external_research_route,
        runtime_policy.research_candidate_id, runtime_policy.research_lifecycle,
        research_policy.action_authority, research_policy.initial_search_provider,
    )
