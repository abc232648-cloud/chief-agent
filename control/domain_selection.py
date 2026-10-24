"""Atomic workspace selection; does not grant capabilities or enable production."""
import json
import time
from .agents import AgentControls
from database.store_extensions import add_audit


def normalize(body):
    if not isinstance(body, dict) or set(body) != {'selection'} or not isinstance(body['selection'], str):
        raise ValueError('Supply only selection: job_only, farm_only, or both.')
    selection = body['selection']
    if selection not in {'job_only', 'farm_only', 'both'}:
        raise ValueError('Selection must be job_only, farm_only, or both.')
    return selection


def current_selection(store):
    with store._connect() as con:
        row = con.execute("SELECT value FROM control_state WHERE key='domain_selection'").fetchone()
        return row[0] if row else None


def select(store, registry, body, *, authorize):
    selection = normalize(body)
    # Constructor preparation must precede the caller-owned write transaction.
    controls = AgentControls(store, registry)
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute("SELECT value FROM control_state WHERE key='domain_selection'").fetchone()
        previous = row[0] if row else None
        # Repeat authorization after serializing state reads: a concurrent first
        # selection must not turn a later change into an insensitive operation.
        authorize(previous is not None and previous != selection)
        status = 'UNCHANGED'
        if previous != selection:
            selected = {'jobs', 'farming'} if selection == 'both' else {('jobs' if selection == 'job_only' else 'farming')}
            for domain in ('jobs', 'farming'):
                existing = con.execute('SELECT enabled FROM agent_controls WHERE domain=?', (domain,)).fetchone()
                enabled = domain in selected
                # An already selected agent keeps its pause/autostart preferences.
                if previous is not None and bool(existing[0]) == enabled:
                    continue
                controls.change(domain, {'enabled': enabled, 'autostart': enabled, 'running': enabled}, con=con)
            con.execute("INSERT OR REPLACE INTO control_state VALUES('domain_selection',?)", (selection,))
            add_audit(store, 'domains', 'Configured workspace domain focus', actor='user',
                      data={'domain_selection': selection}, con=con)
            con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',
                        ('system', 'domain_selection', json.dumps({'selection': selection}), time.time()))
            status = 'CONFIGURED'
    # Do not report success until commit and connection cleanup have succeeded.
    return {'status': status, 'domain_selection': selection,
            'message': 'Workspace selection saved. Required domain setup and existing safety controls still apply.'}
