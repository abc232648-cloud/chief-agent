"""Versioned private operational snapshots and quarantined offline restores.

No Store import, worker startup, network operation, credential loading, schema
migration or live restore. Output parents must be operator-controlled private
storage; application hashes detect corruption, not a hostile storage owner.
"""
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import time
import uuid

from .restore_guard import QUARANTINE_APPLICATION_ID, assert_not_quarantined, marker_path
from .time_integrity import utc_now, utc_text

FORMAT_VERSION = 1
MAX_MANIFEST = 8 * 1024 * 1024


class BackupError(ValueError):
    pass


def digest_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _sha(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise BackupError('Expected a lowercase SHA-256 identity.')
    return value


def _relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value or '\x00' in value:
        raise BackupError('Unsafe relative path.')
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(p in {'.', '..'} for p in path.parts):
        raise BackupError('Unsafe relative path.')
    for part in path.parts:
        if part.endswith((' ', '.')) or part.split('.')[0].upper() in {
            'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))
        }:
            raise BackupError('Unsafe platform path.')
    return path


def _plain(path, *, file=False):
    """Reject symlink/junction/reparse ancestors instead of following them."""
    path = Path(os.path.abspath(path))
    for entry in (path, *path.parents):
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise BackupError('Links and reparse points are not accepted.')
    if file and not path.is_file():
        raise BackupError('Expected a regular file.')
    return path


def _load_json(path):
    path = _plain(path, file=True)
    if path.stat().st_size > MAX_MANIFEST:
        raise BackupError('Manifest exceeds the size limit.')
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise BackupError('Duplicate manifest field.')
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError('Invalid manifest.') from exc


def source_identity(root, manifest_path):
    """Verify the explicit release manifest; never recursively archive runtime data."""
    root = _plain(root)
    manifest = _load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise BackupError('Source manifest must be an object.')
    files = manifest.get('files')
    if not isinstance(files, dict) or not files:
        raise BackupError('Source manifest has no file identities.')
    for name, expected in files.items():
        path = _plain(root / _relative(name), file=True)
        if digest_file(path) != _sha(expected):
            raise BackupError('Source file differs from the release manifest.')
    tree = hashlib.sha256('\n'.join(name + '\0' + files[name] for name in sorted(files)).encode()).hexdigest()
    if tree != manifest.get('source_tree_sha256'):
        raise BackupError('Source tree identity mismatch.')
    return tree


def _connect_ro(path, *, standalone=False):
    uri = Path(path).resolve().as_uri() + '?mode=ro' + ('&immutable=1' if standalone else '')
    con = sqlite3.connect(uri, uri=True, timeout=0.1)
    con.execute('PRAGMA query_only=ON')
    con.execute('PRAGMA trusted_schema=OFF')
    return con


def database_identity(path):
    """Read standalone snapshot metadata, never initialize or migrate a Store."""
    with closing(_connect_ro(_plain(path, file=True), standalone=True)) as con:
        if con.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise BackupError('SQLite integrity check failed.')
        if con.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise BackupError('SQLite foreign key check failed.')
        schema = con.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE sql IS NOT NULL ORDER BY type,name").fetchall()
        counts = {}
        for (name,) in con.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"):
            quoted = '"' + name.replace('"', '""') + '"'
            counts[name] = con.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0]
        return {
            'schema_sha256': hashlib.sha256(_json_bytes(schema)).hexdigest(),
            'schema_version': con.execute('PRAGMA schema_version').fetchone()[0],
            'user_version': con.execute('PRAGMA user_version').fetchone()[0],
            'application_id': con.execute('PRAGMA application_id').fetchone()[0],
            'table_counts': counts,
        }


def _write(path, data):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _cleanup_owned(stage, parent):
    # Only our freshly created staging directory may be removed recursively.
    if stage.parent.resolve() != parent.resolve() or not stage.name.startswith('.incomplete-'):
        raise BackupError('Refusing cleanup outside staging boundary.')
    if stage.exists():
        shutil.rmtree(stage)


def _asset_input(item):
    if not isinstance(item, dict) or set(item) != {'path', 'logical_path', 'kind'}:
        raise BackupError('Each asset needs path, logical_path and kind.')
    if item['kind'] not in {'document', 'provenance', 'configuration'}:
        raise BackupError('Unsupported asset classification.')
    logical = str(_relative(item['logical_path']))
    source = _plain(item['path'], file=True)
    for part in (*source.parts, *PurePosixPath(logical).parts):
        lowered = part.lower()
        if lowered.startswith('.env') or lowered in {'site-sessions', 'email-settings.json'} or any(
            word in lowered for word in ('credential', 'password', 'secret', 'cookie', 'session')
        ):
            raise BackupError('Credential/session files require separate protected recovery.')
    if source.suffix.lower() not in {'.txt', '.md', '.json', '.pdf', '.docx', '.html', '.csv', '.yaml', '.yml', '.toml', '.ini'}:
        raise BackupError('Unsupported asset file type.')
    return source, logical


def create_backup(database, destination, *, source_root, source_manifest,
                  private_storage_confirmed=False, assets=(), writers_quiesced=False,
                  timeout_seconds=30):
    """Return pinned identities. Quiescence is an operator assertion for file sets."""
    if not private_storage_confirmed:
        raise BackupError('Private operational storage must be confirmed.')
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise BackupError('Backup deadline must be finite and positive.')
    assets = list(assets)
    if assets and not writers_quiesced:
        raise BackupError('Database-plus-files capture requires quiesced writers.')
    database = _plain(database, file=True)
    assert_not_quarantined(database)
    destination = Path(os.path.abspath(destination))
    parent = _plain(destination.parent)
    if destination.name.startswith('.incomplete-'):
        raise BackupError('Reserved staging destination.')
    if destination.exists() or destination.is_symlink():
        raise BackupError('Backup destination must not exist.')
    source_sha = source_identity(source_root, source_manifest)
    inputs = [(item, *_asset_input(item)) for item in assets]
    if len({logical.casefold() for _, _, logical in inputs}) != len(inputs):
        raise BackupError('Duplicate asset identity.')
    backup_id = str(uuid.uuid4())
    stage = parent / ('.incomplete-' + backup_id)
    stage.mkdir(mode=0o700)
    started = utc_text(utc_now())
    try:
        snapshot = stage / 'state.sqlite3'
        deadline = time.monotonic() + timeout_seconds
        def progress(status, remaining, total):
            if time.monotonic() >= deadline:
                raise TimeoutError('SQLite backup deadline exceeded.')
        with closing(_connect_ro(database)) as src, closing(sqlite3.connect(snapshot)) as dest:
            src.backup(dest, pages=64, progress=progress, sleep=0.01)
            dest.execute('PRAGMA journal_mode=DELETE')
        os.chmod(snapshot, 0o600)
        identity = database_identity(snapshot)
        entries = {'state.sqlite3': {'sha256': digest_file(snapshot), 'size': snapshot.stat().st_size}}
        provenance = []
        for item, path, logical in inputs:
            relative = 'assets/' + logical
            target = stage / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            before = (path.stat().st_size, path.stat().st_mtime_ns, digest_file(path))
            with path.open('rb') as src, target.open('xb') as dest:
                shutil.copyfileobj(src, dest)
                dest.flush()
                os.fsync(dest.fileno())
            os.chmod(target, 0o600)
            after = (path.stat().st_size, path.stat().st_mtime_ns, digest_file(path))
            if before != after or digest_file(target) != before[2]:
                raise BackupError('Selected file changed during capture.')
            entries[relative] = {'sha256': before[2], 'size': before[0]}
            provenance.append({'original_path': str(path), 'logical_path': logical, 'kind': item['kind']})
        # Ensure DB payload has reached the OS before publishing the manifest.
        with snapshot.open('r+b') as stream:
            os.fsync(stream.fileno())
        manifest = {
            'format_version': FORMAT_VERSION, 'backup_id': backup_id,
            'started_at': started, 'completed_at': utc_text(utc_now()),
            'clock': {'quality': 'UNKNOWN', 'offset_seconds': None, 'uncertainty_seconds': None},
            'source_tree_sha256': source_sha, 'sqlite_runtime': sqlite3.sqlite_version,
            'database': identity, 'files': entries, 'assets': provenance,
            'coverage': 'database-and-selected-files' if assets else 'database-only',
            'writers_quiesced': bool(writers_quiesced),
            'secrets_and_sessions': 'EXCLUDED_FILES_REPROVISION_SEPARATELY',
            'retention': 'MANUAL_NO_AUTOMATIC_DELETION',
        }
        _write(stage / 'manifest.json', _json_bytes(manifest))
        pin = digest_file(stage / 'manifest.json')
        _write(stage / 'COMPLETE', (pin + '\n').encode('ascii'))
        verify_backup(stage, manifest_sha256=pin, expected_source=source_sha,
                      expected_schema=identity['schema_sha256'], _staging=True)
        if destination.exists():
            raise BackupError('Backup destination appeared during capture.')
        stage.rename(destination)
        return {'backup_id': backup_id, 'manifest_sha256': pin, 'source_tree_sha256': source_sha,
                'schema_sha256': identity['schema_sha256'], 'coverage': manifest['coverage']}
    except BaseException:
        _cleanup_owned(stage, parent)
        raise


def verify_backup(directory, *, manifest_sha256, expected_source, expected_schema, _staging=False):
    root = _plain(directory)
    if root.name.startswith('.incomplete-') and not _staging:
        raise BackupError('Staging is not a published backup.')
    manifest_path = _plain(root / 'manifest.json', file=True)
    if digest_file(manifest_path) != _sha(manifest_sha256):
        raise BackupError('Manifest identity mismatch.')
    complete = _plain(root / 'COMPLETE', file=True)
    if complete.stat().st_size != 65 or complete.read_text(encoding='ascii') != manifest_sha256 + '\n':
        raise BackupError('Backup is incomplete.')
    manifest = _load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise BackupError('Backup manifest must be an object.')
    if manifest.get('format_version') != FORMAT_VERSION:
        raise BackupError('Unsupported backup format.')
    if manifest.get('source_tree_sha256') != _sha(expected_source):
        raise BackupError('Unexpected source identity.')
    if not isinstance(manifest.get('database'), dict) or manifest['database'].get('schema_sha256') != _sha(expected_schema):
        raise BackupError('Unexpected schema identity.')
    try:
        uuid.UUID(manifest['backup_id'])
        utc_text(manifest['started_at'])
        utc_text(manifest['completed_at'])
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupError('Invalid backup identity/time metadata.') from exc
    files = manifest.get('files')
    if not isinstance(files, dict) or 'state.sqlite3' not in files:
        raise BackupError('Missing snapshot inventory.')
    if len({name.casefold() for name in files}) != len(files):
        raise BackupError('Duplicate file identity.')
    for name, entry in files.items():
        relative = _relative(name)
        if not isinstance(entry, dict) or type(entry.get('size')) is not int or entry['size'] < 0:
            raise BackupError('Invalid payload metadata.')
        if name != 'state.sqlite3' and (relative.parts[0] != 'assets' or len(relative.parts) < 2):
            raise BackupError('Unsupported backup payload.')
        path = _plain(root / relative, file=True)
        if path.stat().st_size != entry.get('size') or digest_file(path) != _sha(entry.get('sha256')):
            raise BackupError('Backup payload integrity mismatch.')
    actual = set()
    for path in root.rglob('*'):
        _plain(path)
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != set(files) | {'manifest.json', 'COMPLETE'}:
        raise BackupError('Unlisted files or database sidecars present.')
    identity = database_identity(root / 'state.sqlite3')
    if identity != manifest['database']:
        raise BackupError('Database schema/version/count identity mismatch.')
    if identity['application_id'] == QUARANTINE_APPLICATION_ID:
        raise BackupError('A quarantined restore is not an operational backup source.')
    return manifest


def restore_isolated(directory, destination, *, manifest_sha256, expected_source,
                     expected_schema, private_storage_confirmed=False):
    if not private_storage_confirmed:
        raise BackupError('Private isolated storage must be confirmed.')
    manifest = verify_backup(directory, manifest_sha256=manifest_sha256,
                             expected_source=expected_source, expected_schema=expected_schema)
    destination = Path(os.path.abspath(destination))
    parent = _plain(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise BackupError('Restore destination must not exist; live restore is unsupported.')
    stage = parent / ('.incomplete-' + str(uuid.uuid4()))
    stage.mkdir(mode=0o700)
    try:
        for name, entry in manifest['files'].items():
            target = stage / name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with _plain(Path(directory) / name, file=True).open('rb') as src, target.open('xb') as dest:
                shutil.copyfileobj(src, dest)
                dest.flush()
                os.fsync(dest.fileno())
            os.chmod(target, 0o600)
            if digest_file(target) != entry['sha256'] or target.stat().st_size != entry['size']:
                raise BackupError('Backup changed while restoring.')
        database = stage / 'state.sqlite3'
        if database_identity(database) != manifest['database']:
            raise BackupError('Restored database does not match snapshot.')
        marker = {'format_version': 1, 'status': 'QUARANTINED', 'backup_id': manifest['backup_id'],
                  'manifest_sha256': manifest_sha256, 'source_tree_sha256': expected_source,
                  'original_application_id': manifest['database']['application_id'],
                  'received_at': utc_text(utc_now()), 'source_at': manifest['completed_at'],
                  'external_actions': 'DISABLED_NO_AUTOMATIC_PROMOTION'}
        _write(marker_path(database), _json_bytes(marker))
        with closing(sqlite3.connect(database)) as con:
            con.execute(f'PRAGMA application_id={QUARANTINE_APPLICATION_ID}')
            con.commit()
        identity = database_identity(database)
        identity['application_id'] = marker['original_application_id']
        if identity != manifest['database']:
            raise BackupError('Quarantine changed database records or schema.')
        _write(stage / 'restore-receipt.json', _json_bytes(marker))
        if destination.exists():
            raise BackupError('Restore destination appeared during verification.')
        stage.rename(destination)
        return {'status': 'QUARANTINED', 'backup_id': manifest['backup_id'],
                'schema_sha256': expected_schema, 'source_tree_sha256': expected_source,
                'table_counts': identity['table_counts'], 'automatic_replay': False}
    except BaseException:
        _cleanup_owned(stage, parent)
        raise
