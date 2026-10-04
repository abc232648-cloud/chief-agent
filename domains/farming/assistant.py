"""Role-scoped questions with optional explicitly qualified live Qwen guidance.

Built-in explanations are labelled as guidance, never represented as model output.
No question or answer creates an action, approval, payment or operational fact.
"""
from . import setup, staff, journal, bookkeeping
from .journal import bounded_text
from identity.service import IdentityService
import time
import uuid


def context(store, principal):
    with store._connect() as con:
        principal = setup.authorize(store, con, principal, 'read')
        role = setup.role(con, principal)
        from .financial_permissions import effective
        can_finance = role == 'OWNER' or (role == 'GENERAL_MANAGER' and effective(con, principal.id)['access'])
    work = staff.overview(store, principal)
    records = journal.overview(store, principal, limit=20)
    result = {'farm_role': role, 'timezone': 'Africa/Lagos',
              'daily_report_schedule': work['daily_report_schedule'],
              'work_item_count': len(work['items']), 'unresolved_work_count': work['open_count'],
              'context_is_excerpt': len(work['items'])>10 or len(records['balances'])>10 or records['record_count']>20,
              'tasks': [{'id': r['id'], 'kind': r['kind'], 'state': r['state'], 'text': r['text'][:180], 'text_truncated': len(r['text'])>180,
                         'due_at': r['due_at'], 'overdue': r['overdue']} for r in work['items'][-10:]],
              'recent_report_ids': [r['payload']['event_id'] for r in records['records']],
              'balances': records['balances'][:10]}
    if can_finance:
        finance = bookkeeping.overview(store, principal)['balances']
        result['bookkeeping_balances'] = finance[:10]
        result['context_is_excerpt'] |= len(finance)>10
    return principal, result


def overview(store, principal):
    principal, data = context(store, principal)
    from .live_ai import status
    return {**status(store, principal), 'farm_role': data['farm_role'],
            'examples': ['What work is pending?', 'How do I record feed?', 'Why is an opening balance missing?'],
            'action_authority': 'NONE'}


def ask(store, principal, payload):
    from operations.correlation import references
    from operations.diagnostics import agent_trace
    trace_id = references().get('request_id') or uuid.uuid4().hex
    started = time.monotonic()
    outcome, model = 'FAILED', 'none'
    try:
        result = _ask(store, principal, payload)
        outcome = 'LIVE' if result['live_ai'] else 'BUILTIN'
        model = 'farming.qwen' if result['live_ai'] else 'builtin'
        return {**result, 'trace_id': trace_id}
    except PermissionError:
        outcome = 'BLOCKED'
        raise
    finally:
        agent_trace(trace_id, outcome, min(86400000, max(0, int((time.monotonic()-started)*1000))), model)


def _ask(store, principal, payload):
    if not isinstance(payload, dict) or set(payload) != {'question'}:
        raise ValueError('Supply only a question; identity and scope come from the server.')
    question = bounded_text(payload['question'], 1500)
    principal, data = context(store, principal)
    from .live_ai import answer as live_answer
    live = live_answer(store, principal, question, data)
    if live is not None:
        return live
    # These rules offer narrow product instructions, not generated advice. Raw
    # questions are not written to audit, ledger, ordinary logs or source evidence.
    lowered = question.casefold()
    answer = None
    if 'feed' in lowered and any(w in lowered for w in ('record', 'report', 'enter')):
        answer = 'Choose Feed received or Feed used, select the registered feed store, enter kilograms, and state whether you measured or estimated the quantity. Use the actual observation time in Lagos. Recording a delivery does not approve a purchase.'
    elif 'opening' in lowered or 'unknown balance' in lowered:
        answer = 'A missing opening balance means the current balance is unknown. Do not assume zero or use the provisional bird estimate. Ask a supervisor to record an explicit measured or estimated opening count.'
    elif 'task' in lowered or 'pending work' in lowered:
        count = data['unresolved_work_count']
        answer = f'Your authorized workspace contains {count} unresolved work items. A completion report still needs supervisor resolution. See Staff work for the assigned items and deadlines.'
    elif 'time' in lowered or 'late' in lowered:
        answer = ('Farm times use Africa/Lagos. See Daily brief and alerts for the configured reporting deadlines.' if data['daily_report_schedule']=='CONFIGURED' else 'Farm times use Africa/Lagos. Daily reporting deadlines are not configured. Record observations when they happen; Farm Agent will not invent a reporting deadline.')
    if answer is None:
        answer = 'Live AI is not connected yet, so I cannot generate an answer to this question. You can currently ask for reporting instructions, opening-balance explanations or your pending work. Ask your supervisor about other work decisions.'
    IdentityService(store).event(principal, 'FARM_ASSISTANT_GUIDANCE', 'farming', None, 'GUIDANCE_ONLY')
    return {'status': 'BUILT_IN_GUIDANCE', 'answer': answer, 'context': data,
            'live_ai': False, 'action_authority': 'NONE', 'external_requests': 0}
