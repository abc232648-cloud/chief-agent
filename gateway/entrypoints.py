"""Reviewed inference purposes. These describe and restrict calls, never grant authority."""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class EntryPoint:
    domain: str
    agent: str
    capability: str
    trigger: str
    data: str
    effects: str
    max_output_tokens: int


def _job(trigger, data, effects, tokens):
    return EntryPoint('jobs', 'jobs-worker', 'jobs.execute', trigger, data, effects, tokens)


ENTRY_POINTS = MappingProxyType({
    'jobs.plan': _job('Queued human instruction or authorized scheduled command',
        'Instruction, configured source URLs and enabled profile metadata',
        'Proposed plan only; action executor independently enforces policy and approvals', 512),
    'jobs.extract': _job('Approved listing discovery visits a verified source',
        'Bounded public page text, title and links',
        'Candidate listings; URLs and records still require validation', 768),
    'jobs.discover_sources': _job('Source discovery instruction',
        'Job search instruction', 'Source suggestions remain unverified; no automatic trusted status', 512),
    'jobs.analyze': _job('Job analysis pipeline',
        'Job record and supplied candidate facts', 'Validated analysis and summary, not action authorization', 768),
    'jobs.draft': _job('Application drafting instruction',
        'Job, confirmed candidate facts and bounded selected CV text',
        'Evidence-checked draft; no submission permission', 768),
    'jobs.connection_test': _job('Explicit operator gateway smoke command',
        'Synthetic text only', 'Connection response only', 32),
    'farming.connection_test': EntryPoint('farming', 'farm-assistant', 'farming.assistant.ask',
        'Owner explicitly qualifies a saved connection', 'Synthetic text only',
        'Configuration may activate only after Owner reauthorization; no action authority', 30),
    'farming.ask': EntryPoint('farming', 'farm-assistant', 'farming.assistant.ask',
        'Authorized human asks a work question', 'Question and role-scoped Farm context',
        'Guidance only; no tools, operational record writes, approvals or equipment actions', 600),
})


def validate_purpose(request, assignment, capability):
    # Existing third-party/legacy Python consumers can omit purpose. All in-tree
    # production call sites are explicitly labeled and enforced by an inventory test.
    if request.purpose is None:
        return
    if not isinstance(request.purpose, str) or request.purpose not in ENTRY_POINTS:
        raise PermissionError('Unregistered inference purpose.')
    entry = ENTRY_POINTS[request.purpose]
    if (assignment.domain, assignment.agent, capability) != (entry.domain, entry.agent, entry.capability):
        raise PermissionError('Inference purpose does not belong to this consumer.')
    if type(request.max_tokens) is not int or not 1 <= request.max_tokens <= entry.max_output_tokens:
        raise ValueError('Inference output limit exceeds the reviewed purpose.')
