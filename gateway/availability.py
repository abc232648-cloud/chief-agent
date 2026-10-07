"""Lazy provider access: credential failures must not stop unrelated work."""
import logging
import os
from .errors import ProviderUnavailable, InvalidProviderResponse, PaidRouteBlocked
from private_secrets.service import resolve_configured

MESSAGES = {
    'MISSING_KEY': 'No API key is configured. Add a private provider connection.',
    'KEY_UNAVAILABLE': 'The configured key cannot be accessed. Review private credential setup.',
    'AUTH_REJECTED': 'Authentication was rejected. Check whether the key was revoked or expired.',
    'ACCESS_DENIED': 'The provider denied access. Review account and model permissions.',
    'RATE_LIMITED': 'The provider returned a rate or quota limit. Check account limits before retrying.',
    'MODEL_UNAVAILABLE': 'The configured model was not found. Review the model assignment.',
    'UNAVAILABLE': 'The provider could not serve this request. Review its connection and status.',
    'READY': 'A model request succeeded. This does not qualify pricing or answer quality.',
}

def record_observation(store, label, code, *, domain="jobs"):
    if store is None:
        return
    title = label + ' model connection'
    body = code + ': ' + MESSAGES[code]
    try:
        from control.notifications import initialize
        initialize(store)
        with store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            previous = con.execute('SELECT body FROM notifications WHERE title=? ORDER BY id DESC LIMIT 1', (title,)).fetchone()
            if previous and previous[0] == body:
                return
            con.execute('INSERT INTO notifications(title,body,severity,domain,related_page,presented) VALUES(?,?,?,?,?,0)',
                        (title, body, 'INFO' if code == 'READY' else 'ACTION_REQUIRED', domain, 'models'))
    except Exception:
        # Diagnostics failure must not expose secrets or kill unrelated work.
        logging.getLogger(__name__).warning('Provider diagnostic could not be persisted.')


class AvailableProvider:
    def __init__(self, factory, model, key_name, label, *, store=None):
        self.factory, self.model, self.key_name = factory, model, key_name
        self.label, self.store, self.status = label, store, None
        # Inspect only after routing confirms this model is eligible.

    def _notice(self, code):
        self.status = code
        record_observation(self.store, self.label, code)

    def _key(self):
        try:
            key = resolve_configured(os.environ, self.key_name, consumer='jobs.gateway', domain='jobs').strip()
        except Exception:
            self._notice('KEY_UNAVAILABLE')
            return None
        if not key:
            self._notice('MISSING_KEY')
            return None
        return key

    def inspect(self):
        """Local credential observation, never a provider qualification."""
        self._key()

    def generate(self, request):
        key = self._key()
        if not key:
            raise ProviderUnavailable('Model credentials unavailable; see notifications.')
        client = None
        try:
            client = self.factory(key, self.model)
            response = client.generate(request)
            self._notice('READY')
            return response
        except (InvalidProviderResponse, PaidRouteBlocked):
            self._notice('UNAVAILABLE')
            raise
        except Exception as exc:
            current, status = exc, None
            for _ in range(5):
                if current is None:
                    break
                candidate = getattr(current, 'status_code', None)
                if isinstance(candidate, int):
                    status = candidate
                    break
                current = current.__cause__ or current.__context__
            code = {401: 'AUTH_REJECTED', 403: 'ACCESS_DENIED', 404: 'MODEL_UNAVAILABLE', 429: 'RATE_LIMITED'}.get(status, 'UNAVAILABLE')
            self._notice(code)
            raise ProviderUnavailable(MESSAGES[code]) from None
        finally:
            sdk = getattr(client, 'client', None)
            if sdk is not None and hasattr(sdk, 'close'):
                try:
                    sdk.close()
                except Exception:
                    pass
