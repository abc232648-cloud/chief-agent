"""Strict machine-readable release manifest parsing.

A manifest describes compatibility and required evidence. It never activates an
update, migrates a database, downloads software or reads operational secrets.
"""
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import PurePosixPath
import re

from capabilities.contracts import Node, version
from .contracts import MigrationRequirement, ReleaseManifest, RuntimeRequirement

FORMAT_VERSION = 1
_SHA = re.compile(r'^[0-9a-f]{64}$')
_ID = re.compile(r'^[a-z0-9][a-z0-9._-]{0,127}$')
_RUNTIME = re.compile(r'^[a-z][a-z0-9_-]{1,63}$')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate manifest field: ' + str(key))
        result[key] = value
    return result


def _text(value, label, pattern=None):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError(label + ' must be a nonempty string.')
    value = value.strip()
    if pattern and not pattern.fullmatch(value):
        raise ValueError('Invalid ' + label + '.')
    return value


def _sha(value, label):
    return _text(value, label, _SHA)


def _items(value, label):
    if not isinstance(value, list):
        raise ValueError(label + ' must be a list.')
    if len(value) != len({json.dumps(x, sort_keys=True, separators=(',', ':')) for x in value}):
        raise ValueError(label + ' must not contain duplicates.')
    return value


def _tests(values):
    values = _items(values, 'required_tests')
    result = []
    for value in values:
        value = _text(value, 'test path')
        path = PurePosixPath(value)
        if path.is_absolute() or '..' in path.parts or '\\' in value or ':' in value or len(path.parts) < 2 or path.parts[0] != 'tests' or not path.name.startswith('test_') or path.suffix != '.py' or str(path) != value:
            raise ValueError('Invalid project-relative test path: ' + value)
        result.append(value)
    return tuple(result)


def _timestamp(value):
    value = _text(value, 'created_at')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('created_at must be ISO-8601.') from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('created_at must be timezone-aware.')
    return value


def _runtime(item):
    if not isinstance(item, dict) or set(item) != {'runtime', 'minimum', 'before', 'required'}:
        raise ValueError('Invalid runtime requirement.')
    runtime = _text(item['runtime'], 'runtime', _RUNTIME)
    minimum = _text(item['minimum'], 'runtime minimum')
    before = _text(item['before'], 'runtime before')
    if version(minimum) >= version(before):
        raise ValueError('Runtime version interval must be nonempty.')
    if type(item['required']) is not bool:
        raise ValueError('Runtime required flag must be boolean.')
    return RuntimeRequirement(runtime, minimum, before, item['required'])


def _migration(item):
    keys = {'migration_id', 'from_schema', 'to_schema', 'requires_backup', 'reversible'}
    if not isinstance(item, dict) or set(item) != keys:
        raise ValueError('Invalid migration requirement.')
    mid = _text(item['migration_id'], 'migration_id', _ID)
    before = _sha(item['from_schema'], 'from_schema')
    after = _sha(item['to_schema'], 'to_schema')
    if before == after:
        raise ValueError('Migration must change schema identity.')
    if type(item['requires_backup']) is not bool or type(item['reversible']) is not bool:
        raise ValueError('Migration flags must be boolean.')
    return MigrationRequirement(mid, before, after, item['requires_backup'], item['reversible'])


def load_manifest(data):
    """Load strict JSON bytes/text or an already-decoded mapping."""
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data).decode('utf-8')
    if isinstance(data, str):
        data = json.loads(data, object_pairs_hook=_unique_object)
    if not isinstance(data, dict):
        raise ValueError('Manifest must be a JSON object.')
    expected = {
        'format_version', 'release_id', 'chief_version', 'source_tree_sha256', 'schema_sha256',
        'capability_versions', 'changed_nodes', 'required_tests', 'runtime_requirements',
        'model_requirements', 'migrations', 'rollback_compatible_with', 'benchmark_requirements',
        'private_state_excluded', 'created_at'
    }
    if set(data) != expected:
        missing = sorted(expected - set(data)); extra = sorted(set(data) - expected)
        raise ValueError('Manifest fields differ; missing=%s extra=%s' % (missing, extra))
    if data['format_version'] != FORMAT_VERSION:
        raise ValueError('Unsupported manifest format.')
    release_id = _text(data['release_id'], 'release_id', _ID)
    chief_version = _text(data['chief_version'], 'chief_version')
    version(chief_version)
    source = _sha(data['source_tree_sha256'], 'source_tree_sha256')
    schema = _sha(data['schema_sha256'], 'schema_sha256')
    capabilities = []
    for item in _items(data['capability_versions'], 'capability_versions'):
        if not isinstance(item, dict) or set(item) != {'id', 'version'}:
            raise ValueError('Invalid capability version declaration.')
        identity = _text(item['id'], 'capability id')
        Node('capability', identity)
        ver = _text(item['version'], 'capability version')
        version(ver)
        capabilities.append((identity, ver))
    if len({x[0] for x in capabilities}) != len(capabilities):
        raise ValueError('Duplicate capability version declaration.')
    changed = []
    for key in _items(data['changed_nodes'], 'changed_nodes'):
        key = _text(key, 'changed node')
        kind, sep, identity = key.partition(':')
        if not sep:
            raise ValueError('Changed node must use kind:id.')
        changed.append(Node(kind, identity).key)
    runtimes = tuple(_runtime(x) for x in _items(data['runtime_requirements'], 'runtime_requirements'))
    if len({x.runtime for x in runtimes}) != len(runtimes):
        raise ValueError('Duplicate runtime requirement.')
    models = tuple(_text(x, 'model requirement') for x in _items(data['model_requirements'], 'model_requirements'))
    migrations = tuple(_migration(x) for x in _items(data['migrations'], 'migrations'))
    if len({x.migration_id for x in migrations}) != len(migrations):
        raise ValueError('Duplicate migration requirement.')
    rollback = tuple(_text(x, 'rollback release', _ID) for x in _items(data['rollback_compatible_with'], 'rollback_compatible_with'))
    benchmarks = tuple(_text(x, 'benchmark requirement') for x in _items(data['benchmark_requirements'], 'benchmark_requirements'))
    if type(data['private_state_excluded']) is not bool:
        raise ValueError('private_state_excluded must be boolean.')
    return ReleaseManifest(FORMAT_VERSION, release_id, chief_version, source, schema,
                           tuple(sorted(capabilities)), tuple(sorted(changed)), _tests(data['required_tests']),
                           tuple(sorted(runtimes)), tuple(sorted(models)), tuple(sorted(migrations)),
                           tuple(sorted(rollback)), tuple(sorted(benchmarks)), data['private_state_excluded'],
                           _timestamp(data['created_at']))


def manifest_digest(manifest):
    if not isinstance(manifest, ReleaseManifest):
        raise ValueError('Expected ReleaseManifest.')
    payload = asdict(manifest)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
