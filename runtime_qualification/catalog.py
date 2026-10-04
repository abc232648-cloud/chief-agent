"""Strict loader for pinned runtime candidate research snapshots."""
from datetime import date
import json
from urllib.parse import urlparse

from .contracts import Candidate

_ALLOWED_HOSTS = {'docs.openclaw.ai', 'github.com', 'hermes-agent.nousresearch.com'}


def _object(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError('Duplicate candidate field: ' + str(k))
        result[k] = v
    return result


def _url(value):
    if not isinstance(value, str):
        raise ValueError('Candidate URL must be a string.')
    parsed = urlparse(value)
    if parsed.scheme != 'https' or parsed.hostname not in _ALLOWED_HOSTS or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Candidate URL must be approved HTTPS documentation/release provenance.')
    return value


def load_candidates(data):
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data).decode('utf-8')
    if isinstance(data, str):
        data = json.loads(data, object_pairs_hook=_object)
    if not isinstance(data, dict) or set(data) != {'snapshot_date', 'candidates'}:
        raise ValueError('Runtime candidate catalog requires snapshot_date and candidates.')
    try:
        date.fromisoformat(data['snapshot_date'])
    except Exception as exc:
        raise ValueError('Invalid snapshot_date.') from exc
    if not isinstance(data['candidates'], list) or not data['candidates']:
        raise ValueError('At least one runtime candidate is required.')
    result = []
    for item in data['candidates']:
        expected = {'id','product','version','release_date','release_url','documentation_urls','documented_capabilities'}
        if not isinstance(item, dict) or set(item) != expected:
            raise ValueError('Invalid candidate declaration.')
        if not all(isinstance(item[k], str) and item[k].strip() for k in ('id','product','version','release_date')):
            raise ValueError('Candidate identity fields must be nonempty strings.')
        try: date.fromisoformat(item['release_date'])
        except Exception as exc: raise ValueError('Invalid candidate release_date.') from exc
        docs = item['documentation_urls']; caps = item['documented_capabilities']
        if not isinstance(docs, list) or not docs or not isinstance(caps, list):
            raise ValueError('Candidate docs/capabilities must be lists.')
        docs = tuple(_url(x) for x in docs)
        if len(set(docs)) != len(docs): raise ValueError('Duplicate documentation URL.')
        if any(not isinstance(x,str) or not x.strip() for x in caps) or len(set(caps)) != len(caps):
            raise ValueError('Invalid documented capability list.')
        result.append(Candidate(item['id'], item['product'], item['version'], item['release_date'],
                                _url(item['release_url']), docs, tuple(caps)))
    if len({x.id for x in result}) != len(result):
        raise ValueError('Duplicate candidate ID.')
    return tuple(result)
