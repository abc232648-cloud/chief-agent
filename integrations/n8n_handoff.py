"""One bounded synthetic handshake; no general workflow or data dispatch."""
import http.client
import json
import os
import re
import time

from . import n8n
from control.agents import AgentControls
from identity.service import IdentityService

PREFIX = 'integration.n8n.handshake.'
CONTRACT = 'chief.handshake.v1'
PATH = '/webhook/chief-handshake-v1'
TIMEOUT = 5
MAX_RECORDS = 500


def _configuration():
    config = n8n.configuration()
    token = os.environ.get('CHIEF_N8N_HANDSHAKE_KEY', '')
    if config is None or not 32 <= len(token) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError('Configure the local n8n connection and a separate handshake key of at least 32 characters.')
    if token == config[1]:
        raise ValueError('The handshake key must differ from the inventory API key.')
    return config[0], token


def configured():
    try:
        _configuration()
        return True
    except ValueError:
        return False


def _visible(row, now):
    result = dict(row)
    if result['status'] == 'DISPATCHING' and now >= result['deadline']:
        result['status'] = 'UNKNOWN'
    result['message'] = {
        'DISPATCHING': 'Test request is in progress. It will not be sent again automatically.',
        'COMPLETED': 'Synthetic handshake completed. This does not qualify other workflows.',
        'UNKNOWN': 'No verified completion. Inspect n8n; this operation will not be sent again.',
    }[result['status']]
    return result


def history(store):
    with store._connect() as con:
        rows = con.execute('SELECT value FROM control_state WHERE key LIKE ?', (PREFIX+'%',)).fetchall()
    items = sorted((json.loads(r[0]) for r in rows), key=lambda r:r['created_at'], reverse=True)
    return [_visible(row, time.time()) for row in items[:50]]


def _guard(con, store, controls, principal, domain):
    current = IdentityService(store)._principal(con, principal.session_id)
    if current.id != principal.id or current.role not in {'Owner','Administrator'} or '*' not in current.domains:
        raise PermissionError('Current installation administrator authority is required.')
    row = con.execute('SELECT value FROM control_state WHERE key=?', (n8n.KEY,)).fetchone()
    if not row or row[0] != 'true':
        raise ValueError('Enable the n8n connection first.')
    row = con.execute('SELECT enabled,running FROM agent_controls WHERE domain=?', (domain,)).fetchone()
    if not row or not row['enabled'] or not row['running'] or not controls._components_allowed(domain, con):
        raise PermissionError('The selected domain is paused or disabled.')


def _exchange(parsed, token, payload):
    cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
    connection = cls('127.0.0.1', parsed.port, timeout=TIMEOUT)
    try:
        connection.request('POST', PATH, body=json.dumps(payload).encode(), headers={
            'Content-Type':'application/json', 'Accept':'application/json',
            'X-Chief-Handshake-Key':token})
        response = connection.getresponse()
        if response.status != 200 or response.getheader('Content-Type','').split(';')[0].strip().lower() != 'application/json':
            return False
        chunks = []
        size = 0
        read_until = time.monotonic() + TIMEOUT
        while size <= 4096:
            if time.monotonic() >= read_until:
                return False
            chunk = response.read1(4097-size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        raw = b''.join(chunks)
        if len(raw) > 4096:
            return False
        result = json.loads(raw)
        return result == {'contract':CONTRACT, 'operation_id':payload['operation_id'],
                          'domain':payload['domain'], 'status':'COMPLETED', 'result':'synthetic-handshake-only'}
    except (OSError, http.client.HTTPException, ValueError, UnicodeError):
        return False
    finally:
        connection.close()


def run(store, registry, principal, body):
    if (not isinstance(body,dict) or set(body) != {'operation_id','domain','confirmed'}
            or body['confirmed'] is not True or not isinstance(body['operation_id'],str)
            or not re.fullmatch(r'[a-f0-9]{32}',body['operation_id'])
            or not isinstance(body['domain'],str) or body['domain'] not in registry.domains):
        raise ValueError('Confirm a synthetic test with a unique operation ID and registered domain.')
    if principal is None:
        raise PermissionError('Sign in before running the test.')
    key = PREFIX + body['operation_id']
    controls = AgentControls(store, registry)
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        # Recheck the current session even for an existing receipt.
        current = IdentityService(store)._principal(con, principal.session_id)
        if current.id != principal.id or current.role not in {'Owner','Administrator'} or '*' not in current.domains:
            raise PermissionError('Current installation administrator authority is required.')
        prior = con.execute('SELECT value FROM control_state WHERE key=?',(key,)).fetchone()
        if prior:
            row = json.loads(prior[0])
            if row['domain'] != body['domain'] or row['actor'] != principal.id:
                raise ValueError('Operation ID already belongs to another request.')
            return _visible(row, time.time())
        _guard(con, store, controls, principal, body['domain'])
        parsed, token = _configuration()
        if con.execute('SELECT count(*) FROM control_state WHERE key LIKE ?', (PREFIX+'%',)).fetchone()[0] >= MAX_RECORDS:
            raise ValueError('Handshake history limit reached; review required.')
        now = time.time()
        row = {'operation_id':body['operation_id'], 'domain':body['domain'], 'actor':principal.id,
               'contract':CONTRACT, 'created_at':now, 'deadline':now+15, 'status':'DISPATCHING'}
        con.execute('INSERT INTO control_state(key,value) VALUES(?,?)',(key,json.dumps(row)))
        con.execute('INSERT INTO audit_log(category,actor,action,status,data_json) VALUES(?,?,?,?,?)',
                    ('integration',principal.id,'Dispatch synthetic n8n handshake','DISPATCHING',json.dumps({'operation_id':body['operation_id'],'domain':body['domain'],'contract':CONTRACT})))
    # Intent is durable before I/O. A crash here remains unknown, never auto-retried.
    payload = {'contract':CONTRACT,'operation_id':body['operation_id'],'domain':body['domain'],
               'deadline':row['deadline'],'purpose':'synthetic-handshake-only'}
    verified = _exchange(parsed, token, payload)
    row['status'] = 'COMPLETED' if verified and time.time() < row['deadline'] else 'UNKNOWN'
    row['finished_at'] = time.time()
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        con.execute('UPDATE control_state SET value=? WHERE key=?',(json.dumps(row),key))
        con.execute('INSERT INTO audit_log(category,actor,action,status,data_json) VALUES(?,?,?,?,?)',
                    ('integration',principal.id,'Synthetic n8n handshake outcome',row['status'],json.dumps({'operation_id':body['operation_id']})))
    return _visible(row, time.time())
