"""Local n8n inventory connector. Never activates or executes a workflow."""
import http.client
import json
import os
from urllib.parse import urlsplit
from database.store_extensions import add_audit

KEY = 'integration.n8n.enabled'


def configuration():
    base = os.environ.get('CHIEF_N8N_URL', '').strip()
    token = os.environ.get('CHIEF_N8N_API_KEY', '')
    if not base or not token:
        return None
    try:
        parsed = urlsplit(base)
        if (parsed.scheme not in {'http', 'https'} or parsed.hostname != '127.0.0.1'
                or parsed.username or parsed.password or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment or not parsed.port):
            raise ValueError()
        if len(token) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError()
    except ValueError:
        raise ValueError('Configure n8n on an explicit 127.0.0.1 port with a valid private API key.') from None
    return parsed, token


def enabled(store):
    with store._connect() as con:
        row = con.execute('SELECT value FROM control_state WHERE key=?', (KEY,)).fetchone()
    return bool(row and row[0] == 'true')


def describe(store):
    try:
        configured = configuration() is not None
        problem = None
    except ValueError as exc:
        configured = False
        problem = str(exc)
    from .n8n_handoff import configured as handshake_configured
    return {'configured': configured, 'enabled': enabled(store),
            'handshake_configured':handshake_configured(),
            'status': 'READY_TO_CHECK' if configured else 'NOT_CONFIGURED',
            'problem': problem, 'scope': 'METADATA_AND_SYNTHETIC_HANDSHAKE',
            'execution_available': False,
            'message': 'Connection control, workflow inventory and a synthetic handshake test. General workflow execution and schedule migration are not enabled.'}


def inventory():
    config = configuration()
    if config is None:
        raise ValueError('n8n URL and API key are not configured in private service settings.')
    parsed, token = config
    cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
    connection = cls('127.0.0.1', parsed.port, timeout=5)
    try:
        connection.request('GET', '/api/v1/workflows?limit=100', headers={
            'X-N8N-API-KEY': token, 'Accept': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('n8n connection refused or access unavailable. Check the service and API permissions.')
        body = response.read(1_000_001)
        if len(body) > 1_000_000:
            raise ValueError('n8n workflow inventory exceeds the response limit.')
        data = json.loads(body)
        if not isinstance(data, dict) or not isinstance(data.get('data'), list) or len(data['data']) > 100:
            raise ValueError('Unexpected n8n inventory format.')
        rows = []
        for item in data['data']:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not isinstance(item.get('name'), str) or type(item.get('active')) is not bool:
                raise ValueError('Unexpected n8n workflow metadata.')
            rows.append({'id':item['id'][:200], 'name':item['name'][:200], 'active':item['active']})
        return {'status':'CONNECTED', 'workflows':rows, 'partial':bool(data.get('nextCursor')),
                'message':'Metadata synchronized. Workflow contents, credentials and execution data were not retained.'}
    except (OSError, http.client.HTTPException, UnicodeError, json.JSONDecodeError):
        raise ValueError('n8n connection check failed. No workflows were changed.') from None
    finally:
        connection.close()


def check(store):
    if not enabled(store):
        raise ValueError('Enable the configured n8n connection before synchronizing metadata.')
    result = inventory()
    if not enabled(store):
        raise ValueError('n8n connection was disabled during synchronization.')
    add_audit(store,'integration','Checked n8n workflow metadata',data={'count':len(result['workflows']),'partial':result['partial']})
    return result


def set_enabled(store, value):
    if type(value) is not bool:
        raise ValueError('Enabled must be true or false.')
    if value:
        inventory()  # A successful read is required; this does not start any workflow.
    with store._connect() as con:
        con.execute('INSERT INTO control_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (KEY, json.dumps(value)))
    add_audit(store,'integration','Changed n8n connection availability',data={'enabled':value})
    return describe(store)
