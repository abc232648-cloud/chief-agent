"""Recorded daily operations and in-app reporting alerts; no external dispatch.

Owner schedules take effect tomorrow in Lagos. A report satisfies presence, not
proof that a whole day's work was completed. Corrections remain authoritative.
"""
from datetime import date, datetime, time as clock_time, timedelta
from decimal import Decimal
import json
import re
import time
from . import setup, journal, journal_queries, staff, finance
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text, aware_utc

KIND = 'farm_reporting_schedule_v1'
REPORTS = {'mortality': 'FLOCK', 'eggs_collected': 'FLOCK', 'feed_used': 'FEED_STORE'}


def schedules(con):
    return [json.loads(r[0]) for r in con.execute(
        "SELECT data_json FROM domain_records WHERE domain='farming' AND kind=? ORDER BY id", (KIND,))]


def schedule_status(con):
    today = utc_now().astimezone(setup.LAGOS).date().isoformat()
    history = schedules(con)
    current = [r for r in history if r['effective_on'] <= today]
    return 'CONFIGURED' if current and current[-1]['payload']['requirements'] else 'NOT_CONFIGURED'


def configure(store, principal, payload):
    if not isinstance(payload, dict) or set(payload) != {'event_id', 'expected_revision', 'requirements'}:
        raise ValueError('Supply the schedule identifier, previous revision and requirements.')
    journal.identifier(payload['event_id'])
    if payload['expected_revision'] is not None:
        journal.identifier(payload['expected_revision'])
    requirements = payload['requirements']
    if not isinstance(requirements, list) or len(requirements) > 100:
        raise ValueError('Use at most 100 reporting requirements.')
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = setup.authorize(store, con, principal, 'approve')
        setup.available(store, con)
        history = schedules(con)
        prior = next((r for r in history if r['payload']['event_id'] == payload['event_id']), None)
        if prior:
            if prior['payload'] != payload or prior['actor_id'] != principal.id:
                raise ValueError('Submission identifier conflict.')
            return {'status': 'ALREADY_RECORDED', 'effective_on': prior['effective_on']}
        if payload['expected_revision'] != (history[-1]['payload']['event_id'] if history else None):
            raise ValueError('Reporting schedule changed; refresh before saving.')
        catalog = setup.entities(con)
        seen = set()
        for r in requirements:
            if not isinstance(r, dict) or set(r) != {'entity_id', 'kind', 'due_time', 'assignee'}:
                raise ValueError('Each requirement needs a location, report, deadline and responsible person.')
            for field in ('entity_id', 'assignee'):
                journal.identifier(r[field])
            if not isinstance(r['kind'], str) or r['kind'] not in REPORTS:
                raise ValueError('Choose egg collection, mortality or feed use.')
            if catalog.get(r['entity_id'], {}).get('entity_type') != REPORTS[r['kind']]:
                raise ValueError('Choose a registered flock or feed store appropriate for the report.')
            if not isinstance(r['due_time'], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', r['due_time']):
                raise ValueError('Use a Lagos deadline in HH:MM format.')
            key = (r['entity_id'], r['kind'])
            if key in seen:
                raise ValueError('Only one daily requirement per location and report type.')
            seen.add(key)
            target = con.execute('SELECT enabled,domains FROM human_identities WHERE id=?', (r['assignee'],)).fetchone()
            if not target or not target['enabled'] or not {'farming', '*'} & set(json.loads(target['domains'])):
                raise ValueError('Choose an enabled Farm account as the responsible person.')
        now = utc_now()
        record = {'payload': payload, 'actor_id': principal.id, 'session_id': principal.session_id,
                  'received_at': utc_text(now), 'effective_on': (now.astimezone(setup.LAGOS).date()+timedelta(days=1)).isoformat()}
        con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',
                    ('farming', KIND, json.dumps(record, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_REPORTING_SCHEDULE', 'farming', payload['event_id'], 'RECORDED')
    return {'status': 'RECORDED', 'effective_on': record['effective_on']}


def overview(store, principal, day=''):
    now = utc_now(); today = now.astimezone(setup.LAGOS).date()
    try:
        selected = date.fromisoformat(day) if day else today
        if day and selected.isoformat() != day:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Use an ISO reporting date.') from None
    if selected > today:
        raise ValueError('A daily brief cannot describe a future day.')
    day = selected.isoformat()
    with store._connect() as con:
        con.execute('BEGIN')
        principal = setup.authorize(store, con, principal, 'read')
        role = setup.role(con, principal)
        manager = role in {'OWNER', 'GENERAL_MANAGER', 'SUPERVISOR'}
        catalog = setup.entities(con)
        history = schedules(con)
        eligible = [r for r in history if r['effective_on'] <= day]
        schedule = eligible[-1] if eligible else None
        requirements = schedule['payload']['requirements'] if schedule else []
        # Stream effective records. Never truncate a day's totals to the UI history page.
        metrics = {}; reported = {}; last_received = None
        for row in con.execute("SELECT data_json FROM domain_records d WHERE d.domain='farming' AND d.kind='poultry_journal_v1' AND "+journal_queries.ACTIVE):
            record = json.loads(row[0]); p = record['payload']
            if aware_utc(p['observed_at']).astimezone(setup.LAGOS).date() != selected:
                continue
            key = (p.get('entity_id'), p['kind'])
            # A correction submitted later must not make an on-time original late.
            original = record; visited = set()
            while original['payload']['corrects']:
                parent = original['payload']['corrects']
                if parent in visited:
                    raise ValueError('Journal correction chain needs reconciliation.')
                visited.add(parent)
                original = journal_queries.prior(con, parent)
                if original is None:
                    raise ValueError('Journal correction history is incomplete.')
            reported.setdefault(key, []).append(original['received_at'])
            if not manager and record['actor_id'] != principal.id:
                continue
            key = (journal.stock_key(p), p['kind'])
            m = metrics.setdefault(key, {'location': p['location'], 'entity_id': p.get('entity_id'), 'kind': p['kind'],
                                         'quantity': Decimal(0), 'unit': journal.TYPES[p['kind']][0], 'reports': 0, 'contains_estimates': False})
            m['quantity'] += Decimal(p['quantity']); m['reports'] += 1
            m['contains_estimates'] |= p['basis'] == 'ESTIMATED'
            last_received = max(last_received or '', record['received_at'])
        for m in metrics.values():
            m['quantity'] = format(m['quantity'], 'f')
        checks = []
        for requirement in requirements:
            if not manager and requirement['assignee'] != principal.id:
                continue
            deadline = datetime.combine(selected, clock_time.fromisoformat(requirement['due_time']), setup.LAGOS)
            receipts = reported.get((requirement['entity_id'], requirement['kind']), [])
            status = ('REPORTED_LATE' if min(map(aware_utc, receipts)) > deadline else 'REPORTED') if receipts else ('MISSING' if now >= deadline else 'AWAITING')
            checks.append({**requirement, 'location': catalog[requirement['entity_id']]['name'], 'status': status,
                           'id': day+':'+requirement['entity_id']+':'+requirement['kind']})
        work = [item for item in staff.projection(staff.rows(con)).values()
                if item['state'] != 'RESOLVED' and (manager or principal.id in {item['reporter'], item['assignee']})]
        work = [{k: item[k] for k in ('id','kind','text','state','due_at','severity','overdue')} for item in work]
        balances = journal_queries.overview(con, principal, manager, 0, 1)['balances'] if manager else []
        latest = history[-1] if history else None
        settings = {'can_configure': role == 'OWNER'}
        if role == 'OWNER':
            settings.update(revision=latest['payload']['event_id'] if latest else None,
                            requirements=latest['payload']['requirements'] if latest else [],
                            effective_on=latest['effective_on'] if latest else None,
                            assignees=[{'id':r['id'],'username':r['username']} for r in con.execute('SELECT id,username,domains FROM human_identities WHERE enabled=1') if {'farming','*'} & set(json.loads(r['domains']))])
    result = {'date': day, 'timezone':'Africa/Lagos', 'generated_at':utc_text(now), 'farm_role':role,
              'scope':'FARM' if manager else 'OWN_REPORTS_AND_ASSIGNED_WORK', 'metrics':list(metrics.values()),
              'last_report_received_at':last_received, 'reporting':checks,
              'schedule_status':'CONFIGURED' if requirements else 'NOT_CONFIGURED', 'settings':settings,
              'current_work':work, 'current_balances':balances, 'external_notifications':'NOT_CONFIGURED',
              'notice':'Quantities are recorded observations, not verified full-day totals. No report means unknown, not zero. Reporting checks require at least one matching report; they do not certify a full day. Work and stock balances show the current state, even when viewing a past day.'}
    result['alerts'] = [{'id':r['id'],'type':'MISSING_REPORT','text':r['location']+' — '+r['kind'].replace('_',' ')+' report missing after '+r['due_time']+' Lagos'} for r in checks if r['status']=='MISSING']
    result['alerts'] += [{'id':str(b.get('entity_id') or b['location'])+':'+b['unit'], 'type':'STOCK_RECONCILIATION', 'text':b['location']+' — '+b['unit']+' opening balance or reconciliation needed'} for b in balances if b['needs_reconciliation']]
    result['alerts'] += [{'id':w['id'],'type':'OVERDUE_TASK' if w['overdue'] else 'OPEN_INCIDENT','text':w['text']} for w in work if w['overdue'] or w['kind']=='INCIDENT']
    if role in {'OWNER','GENERAL_MANAGER'} and setup.overview(store, principal)['can_finance']:
        daily = finance.report(store, principal, start=day, end=day)
        current = finance.report(store, principal, include_all_balances=True)
        result['finance'] = {'daily':daily['summaries'], 'current':current['summaries'], 'overdue':[b for b in current['balances'] if b['overdue']], 'notice':daily['notice']}
        if role == 'OWNER':
            result['alerts'] += [{'id':'approval:'+currency,'type':'PURCHASE_APPROVAL','text':values['pending_approvals']+' purchase requests awaiting a decision ('+currency+')'} for currency,values in current['summaries'].items() if int(values['pending_approvals'])]
            result['alerts'] += [{'id':b['id'],'type':'OVERDUE_FINANCE','text':b['counterparty']+' — outstanding '+b['currency']+' '+b['outstanding_minor']+' minor units'} for b in result['finance']['overdue']]
    return result
