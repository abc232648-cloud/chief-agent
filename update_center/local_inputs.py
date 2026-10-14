"""Bounded local inputs and provisioned publisher policy; no application wiring."""
import base64
import json
from pathlib import Path
from compatibility.manifest import _unique_object
from operations.backup import _plain
from update_center.release_trust import REPOSITORY, PublisherKey


def read_bounded(filename, limit):
    path = Path(filename)
    if not path.is_absolute():
        raise ValueError('Absolute operator input required.')
    _plain(path, file=True)
    with path.open('rb') as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError('Operator input exceeds limit.')
    return value


def load_policy(filename):
    value = json.loads(read_bounded(filename, 16384), object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or set(value) != {'version', 'repository', 'minimum_sequence', 'keys'}:
        raise ValueError('Invalid publisher policy.')
    if type(value['version']) is not int or value['version'] != 1 or value['repository'] != REPOSITORY:
        raise ValueError('Invalid publisher policy.')
    if type(value['minimum_sequence']) is not int or value['minimum_sequence'] < 0:
        raise ValueError('Invalid publisher policy.')
    if not isinstance(value['keys'], list) or not 1 <= len(value['keys']) <= 16:
        raise ValueError('Invalid publisher policy.')
    keys = []
    for item in value['keys']:
        if not isinstance(item, dict) or set(item) != {'key_id', 'public_key', 'valid_from', 'valid_until', 'revoked'}:
            raise ValueError('Invalid publisher key.')
        if type(item['revoked']) is not bool:
            raise ValueError('Invalid publisher key.')
        key = base64.b64decode(item['public_key'], validate=True)
        if len(key) != 32:
            raise ValueError('Invalid publisher key.')
        keys.append(PublisherKey(item['key_id'], key, item['valid_from'], item['valid_until'], item['revoked']))
    return tuple(keys), value['minimum_sequence']


