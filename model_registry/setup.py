"""Inactive installation intake; deliberately not a live routing registry.

No assignments, installation allowlists or authoritative records are modified.
Secret-bearing records live outside source and ordinary backup asset allowlists.
"""
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
import urllib.request
import urllib.error
import ssl
import socket
from operations.backup import _plain
from operations.time_integrity import utc_now, utc_text
from private_secrets import imported
from private_secrets.service import _private_read

ENDPOINTS = {'groq': 'https://api.groq.com/openai/v1/models',
             'mistral': 'https://api.mistral.ai/v1/models'}
FIELDS = ('id', 'provider', 'provider_model', 'cost', 'state', 'created_at', 'created_by', 'key_configured')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ModelSetup:
    def __init__(self, state_root):
        root = Path(state_root)
        if not root.is_absolute():
            raise ValueError('An explicit private instance directory is required.')
        _plain(root)
        source = Path(__file__).resolve().parents[1]
        if root.resolve().is_relative_to(source):
            raise ValueError('Model credentials must be stored outside application source.')
        self.root = root/'model-credentials'

    def _directory(self):
        self.root.mkdir(mode=0o700, exist_ok=True)
        _plain(self.root)
        if os.name != 'nt':
            info = self.root.stat()
            if info.st_mode & 0o077 or info.st_uid != os.geteuid():
                raise PermissionError('Model credential directory must be private to the service account.')

    def _record(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'imported-[a-f0-9]{32}', identity):
            raise ValueError('Invalid registration identity.')
        path = self.root/identity
        _plain(path)
        value = json.loads(_private_read(path, 'registration.json'))
        if value['id'] != identity or value['state'] != 'DISABLED':
            raise ValueError('Invalid inactive registration.')
        return path, value

    def list(self):
        if not self.root.exists():
            return []
        self._directory()
        rows = []
        for path in sorted(self.root.iterdir()):
            if path.name.startswith('imported-'):
                _, value = self._record(path.name)
                rows.append({key: value[key] for key in FIELDS})
        return rows

    def add(self, body, *, actor):
        if set(body) != {'provider', 'provider_model', 'cost', 'api_key'}:
            raise ValueError('Only provider, model identifier, pricing status and API key are accepted.')
        provider, model, cost = body['provider'], body['provider_model'], body['cost']
        if not isinstance(provider, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', provider):
            raise ValueError('Provider must be a short lowercase identifier.')
        if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:-]{0,199}', model):
            raise ValueError('Enter a model identifier (maximum 200 characters).')
        if cost not in {'FREE', 'PAID', 'UNKNOWN'}:
            raise ValueError('Choose a pricing status; this is an unverified operator declaration.')
        key = body['api_key']
        if not isinstance(key, str):
            raise ValueError('API key must be text.')
        if key and (key in provider or key in model):
            raise ValueError('The API key must appear only in the private API key field.')
        self._directory()
        identity = 'imported-'+uuid.uuid4().hex
        stage = Path(tempfile.mkdtemp(prefix='.incomplete-', dir=self.root))
        try:
            backend = imported.provision(stage, key) if key else None
            record = dict(id=identity, provider=provider, provider_model=model, cost=cost,
                          state='DISABLED', created_at=utc_text(utc_now()), created_by=actor,
                          key_configured=bool(key), backend=backend)
            fd = os.open(stage/'registration.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(record, stream); stream.flush(); os.fsync(stream.fileno())
            os.rename(stage, self.root/identity)
            return {field: record[field] for field in FIELDS}
        except Exception:
            # Only known files in our newly allocated private stage; no recursive deletion.
            for name in ('key.cred', 'key.dpapi', 'registration.json'):
                (stage/name).unlink(missing_ok=True)
            stage.rmdir()
            raise

    def check_connection(self, identity):
        path, record = self._record(identity)
        endpoint = ENDPOINTS.get(record['provider'])
        if not endpoint or not record['key_configured']:
            raise ValueError('A saved key and a supported Groq or Mistral adapter are required.')
        def failed(code, message):
            return {'status':'CHECK_FAILED','code':code,'message':message+' No model was activated.'}
        try:
            key = imported.resolve(path, record['backend'])
        except Exception:
            return failed('KEY_UNAVAILABLE','The stored key could not be unlocked by this Windows/Linux account. Save a new connection under the account running Chief.')
        try:
            request = urllib.request.Request(endpoint, headers={'Authorization': 'Bearer '+key, 'Accept': 'application/json'})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=10) as response:
                data = response.read(1024*1024+1)
                if response.status != 200 or len(data) > 1024*1024:
                    raise ValueError()
                models = json.loads(data)['data']
                if not isinstance(models, list):
                    raise ValueError()
                found = any(isinstance(item, dict) and item.get('id') == record['provider_model'] for item in models)
            return {'status': 'CONNECTED', 'model_listed': found,
                    'message': 'Provider responded. This does not verify pricing, model suitability or action authority. Registration remains inactive.'}
        except urllib.error.HTTPError as exc:
            messages={401:('AUTH_REJECTED','Groq/Mistral rejected authentication. Check or replace the key in the private key field.'),403:('ACCESS_DENIED','The provider denied this account access. Check provider account/project permissions.'),429:('RATE_LIMITED','The provider rate limit was reached. Wait and retry; no fallback was used.')}
            code,message=messages.get(exc.code,('PROVIDER_HTTP_ERROR','The provider returned an unsuccessful HTTP response. Retry later or check provider service status.'))
            exc.close()
            return failed(code,message)
        except (urllib.error.URLError,TimeoutError,ssl.SSLError,socket.timeout) as exc:
            reason=exc.reason if isinstance(exc,urllib.error.URLError) else exc
            if isinstance(reason,ssl.SSLError):return failed('TLS_FAILED','A secure provider connection could not be established. Check the machine clock and trusted certificates; TLS verification remains enabled.')
            return failed('NETWORK_FAILED','The provider could not be reached. Check Internet access, DNS and firewall settings, then retry.')
        except Exception:
            # Provider error bodies, URLs, headers and keys never cross this boundary.
            return failed('INVALID_RESPONSE','The provider response could not be validated. Check provider availability and retry.')
