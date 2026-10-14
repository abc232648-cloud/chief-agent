"""Strict cross-repository release-set metadata and built-client verification."""
import json
from pathlib import Path
import re

from compatibility.manifest import _unique_object
from operations import backup

_RELEASE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}')
_APPS = {'owner': '/owner/', 'staff': '/staff/', 'jobs': '/jobs/'}


def _text(path, limit=65536):
    path = backup._plain(path, file=True)
    raw = path.read_bytes()
    if len(raw) > limit:
        raise ValueError('Release metadata exceeds limit.')
    try:
        return raw.decode('utf-8')
    except UnicodeError as exc:
        raise ValueError('Release metadata must be UTF-8.') from exc


def load_release_set(path):
    """Load one operator-reviewed release set without inferring missing versions."""
    value = json.loads(_text(path), object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or set(value) != {'format_version', 'release_set', 'backend_contract', 'backend', 'clients'}:
        raise ValueError('Invalid release-set fields.')
    if type(value['format_version']) is not int or value['format_version'] != 1:
        raise ValueError('Unsupported release-set format.')
    for field in ('release_set', 'backend_contract'):
        if not isinstance(value[field], str) or not _RELEASE.fullmatch(value[field]):
            raise ValueError('Invalid release-set identifier.')
    backend = value['backend']
    if not isinstance(backend, dict) or set(backend) != {'repository', 'release_branch', 'tested_commit', 'tested_source_tree_sha256'}:
        raise ValueError('Invalid backend release metadata.')
    if backend['repository'] != 'abc232648-cloud/chief-agent' or backend['release_branch'] != 'release/2026-10-13-install-closure':
        raise ValueError('Unexpected backend release source.')
    if not re.fullmatch(r'[0-9a-f]{40}', backend['tested_commit'] or ''):
        raise ValueError('Invalid tested backend commit.')
    backup._sha(backend['tested_source_tree_sha256'])
    clients = value['clients']
    if not isinstance(clients, list) or not 2 <= len(clients) <= 3:
        raise ValueError('Invalid release client set.')
    seen = set()
    for item in clients:
        if not isinstance(item, dict) or set(item) != {'app', 'repository', 'release_branch', 'tested_commit', 'tested_source_tree_sha256', 'route', 'required'}:
            raise ValueError('Invalid client release metadata.')
        app = item['app']
        if app in seen or app not in _APPS or item['route'] != _APPS[app]:
            raise ValueError('Invalid or duplicate client route.')
        seen.add(app)
        if item['repository'] not in {'abc232648-cloud/chief-owner-pwa', 'abc232648-cloud/chief-staff-pwa', 'abc232648-cloud/Job-agent-pwa'}:
            raise ValueError('Unexpected client repository.')
        if item['release_branch'] != 'release/2026-10-13-install-closure' or not re.fullmatch(r'[0-9a-f]{40}', item['tested_commit'] or ''):
            raise ValueError('Invalid tested client source.')
        backup._sha(item['tested_source_tree_sha256'])
        if type(item['required']) is not bool:
            raise ValueError('Invalid client requirement flag.')
    if not {'owner', 'staff'} <= seen:
        raise ValueError('Owner and Staff releases are required in every release set.')
    return value


def verify_built_clients(release_set, client_root):
    """Require built companion PWAs to declare the exact backend contract/release set."""
    root = backup._plain(client_root)
    result = {}
    for item in release_set['clients']:
        app = item['app']
        folder = root / app
        if not folder.exists():
            if item['required']:
                raise ValueError('Required companion PWA build is missing.')
            result[app] = 'OPTIONAL_NOT_PRESENT'
            continue
        folder = backup._plain(folder)
        marker = json.loads(_text(folder / 'release.json', 8192), object_pairs_hook=_unique_object)
        expected = {
            'format_version': 1,
            'app': app,
            'release_set': release_set['release_set'],
            'backend_contract': release_set['backend_contract'],
            'route': item['route'],
        }
        if marker != expected:
            raise ValueError('Companion PWA release identity differs.')
        result[app] = 'VERIFIED'
    return result
