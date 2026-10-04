"""Explicit operator-selected browser build; no implicit download or fallback."""
import hashlib
import json
import os
from pathlib import Path
import re
from operations.backup import _plain


def launch_options():
    config = os.environ.get('CHIEF_BROWSER_RUNTIME')
    if not config:
        if os.environ.get('CHIEF_INSTANCE_MODE', '').lower() == 'production':
            raise PermissionError('Production browsing requires a qualified browser runtime.')
        return {}
    path = Path(config)
    if not path.is_absolute():
        raise ValueError('Browser runtime configuration must be absolute.')
    _plain(path, file=True)
    if path.stat().st_size > 8192:
        raise ValueError('Browser runtime configuration exceeds limit.')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or set(data) != {'executable', 'sha256', 'version'}:
        raise ValueError('Invalid browser runtime configuration.')
    executable = Path(data['executable'])
    if not executable.is_absolute() or not re.fullmatch('[0-9a-f]{64}', data['sha256']) or not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', data['version']):
        raise ValueError('Invalid browser runtime identity.')
    _plain(executable, file=True)
    with executable.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != data['sha256']:
            raise PermissionError('Browser runtime identity changed; requalification required.')
    # Refuse a host which cannot support Chrome's sandbox; never retry unsandboxed.
    return {'executable_path': str(executable), 'chromium_sandbox': True}
