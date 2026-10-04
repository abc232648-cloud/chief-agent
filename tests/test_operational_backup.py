from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from operations import backup as b
from operations.restore_guard import RestoreQuarantined, marker_path, QUARANTINE_APPLICATION_ID
from database.store import Store
from application.composition import default_registry
from control.agents import AgentControls
from domains.storage import initialize


@pytest.fixture
def fixture(tmp_path):
    source = tmp_path / 'release'
    source.mkdir()
    (source / 'example.py').write_text('VERSION = "synthetic"\n')
    files = {'example.py': b.digest_file(source / 'example.py')}
    tree = hashlib.sha256('\n'.join(n + '\0' + h for n, h in sorted(files.items())).encode()).hexdigest()
    release = tmp_path / 'release-manifest.json'
    release.write_text(json.dumps({'files': files, 'source_tree_sha256': tree}))
    store = Store(tmp_path / 'live.db')
    controls = AgentControls(store, default_registry())
    initialize(store)
    controls.change('jobs', {'running': False, 'autostart': False})
    store.queue_command('Synthetic queued work; never execute')
    approved = store.add_action('Synthetic', 'submit_application', payload={'fixture': True})
    store.resolve_action(approved, 'APPROVED')
    interrupted = store.add_action('Synthetic interrupted', 'submit_application')
    with store._connect() as con:
        con.execute("UPDATE actions SET status='EXECUTING' WHERE id=?", (interrupted,))
        con.execute("INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES('farming','synthetic','{}',1)")
        con.execute("INSERT INTO domain_reminders(domain,title,due_at) VALUES('farming','Synthetic reminder',1)")
    args = dict(source_root=source, source_manifest=release, private_storage_confirmed=True)
    return SimpleNamespace(root=tmp_path, store=store, controls=controls, args=args, tree=tree)


def capture(fixture, name='backup', **extra):
    dest = fixture.root / name
    result = b.create_backup(fixture.store.path, dest, **(fixture.args | extra))
    verify = {'manifest_sha256': result['manifest_sha256'], 'expected_source': result['source_tree_sha256'],
              'expected_schema': result['schema_sha256']}
    return dest, result, verify


def rows(path):
    with closing(sqlite3.connect(path)) as con:
        return {name: con.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall()
                for (name,) in con.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")}


def repin(directory, verify):
    digest = b.digest_file(directory / 'manifest.json')
    (directory / 'COMPLETE').write_text(digest + '\n')
    return verify | {'manifest_sha256': digest}


def test_consistent_wal_includes_commits_excludes_uncommitted(fixture):
    with closing(sqlite3.connect(fixture.store.path)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("INSERT INTO commands(instruction) VALUES('committed in WAL')")
        writer.commit()
        assert Path(str(fixture.store.path) + '-wal').stat().st_size > 0
        writer.execute("INSERT INTO commands(instruction) VALUES('uncommitted')")
        directory, result, verify = capture(fixture)
        manifest = b.verify_backup(directory, **verify)
        with closing(sqlite3.connect(directory / 'state.sqlite3')) as con:
            values = [row[0] for row in con.execute('SELECT instruction FROM commands')]
        assert 'committed in WAL' in values and 'uncommitted' not in values
        assert manifest['coverage'] == 'database-only'
        assert not list(directory.glob('*-wal')) and not list(directory.glob('*-shm'))
        writer.rollback()


def test_online_backup_with_concurrent_writer_is_transaction_consistent(fixture):
    with fixture.store._connect() as con:
        con.execute('CREATE TABLE pairs(id INTEGER PRIMARY KEY, left_value INTEGER, right_value INTEGER, padding BLOB)')
        con.executemany('INSERT INTO pairs VALUES(?,?,?,zeroblob(8192))', [(i, 0, 0) for i in range(160)])
    started, stop = threading.Event(), threading.Event()
    errors = []
    def write():
        try:
            with closing(sqlite3.connect(fixture.store.path, timeout=5)) as con:
                for version in range(1, 100):
                    con.execute('BEGIN IMMEDIATE')
                    con.execute('UPDATE pairs SET left_value=?', (version,))
                    con.execute('UPDATE pairs SET right_value=?', (version,))
                    con.commit()
                    started.set()
                    if stop.wait(0.002):
                        break
        except BaseException as exc:
            errors.append(exc)
            started.set()
    worker = threading.Thread(target=write)
    worker.start()
    try:
        assert started.wait(5)
        directory, _, verify = capture(fixture)
    finally:
        stop.set()
        worker.join(5)
    assert not worker.is_alive() and not errors
    b.verify_backup(directory, **verify)
    with closing(sqlite3.connect(directory / 'state.sqlite3')) as con:
        assert con.execute('SELECT COUNT(*) FROM pairs WHERE left_value != right_value').fetchone()[0] == 0
        assert con.execute('SELECT COUNT(DISTINCT left_value) FROM pairs').fetchone()[0] == 1


@pytest.mark.parametrize('failure', [OSError('synthetic disk failure'), KeyboardInterrupt()])
def test_failed_or_interrupted_backup_does_not_publish(fixture, monkeypatch, failure):
    original = b._write
    def fail(path, data):
        if path.name == 'manifest.json':
            raise failure
        return original(path, data)
    monkeypatch.setattr(b, '_write', fail)
    before = rows(fixture.store.path)
    with pytest.raises(type(failure)):
        capture(fixture)
    assert not (fixture.root / 'backup').exists()
    assert not list(fixture.root.glob('.incomplete-*'))
    assert rows(fixture.store.path) == before


def test_backup_deadline_cleans_staging(fixture, monkeypatch):
    tick = iter([0, 100, 200, 300])
    monkeypatch.setattr(b.time, 'monotonic', lambda: next(tick))
    with pytest.raises(TimeoutError):
        capture(fixture, timeout_seconds=1)
    assert not (fixture.root / 'backup').exists()
    assert not list(fixture.root.glob('.incomplete-*'))


def test_interrupted_staging_not_accepted(fixture):
    directory, _, verify = capture(fixture)
    (directory / 'COMPLETE').unlink()
    with pytest.raises((b.BackupError, OSError)):
        b.verify_backup(directory, **verify)


@pytest.mark.parametrize('mutation', ['payload', 'manifest', 'extra', 'schema', 'corrupt_rehashed'])
def test_corrupt_or_inconsistent_backup_is_rejected(fixture, mutation):
    directory, _, verify = capture(fixture)
    db = directory / 'state.sqlite3'
    if mutation == 'manifest':
        (directory / 'manifest.json').write_text('{}')
    elif mutation == 'extra':
        (directory / 'state.sqlite3-wal').write_bytes(b'unlisted')
    else:
        if mutation == 'schema':
            with closing(sqlite3.connect(db)) as con:
                con.execute('CREATE TABLE unexpected(value TEXT)')
                con.commit()
        else:
            db.write_bytes(b'not sqlite' + db.read_bytes()[10:])
        if mutation in {'schema', 'corrupt_rehashed'}:
            manifest = json.loads((directory / 'manifest.json').read_text())
            manifest['files']['state.sqlite3'] = {'size': db.stat().st_size, 'sha256': b.digest_file(db)}
            (directory / 'manifest.json').write_text(json.dumps(manifest))
            verify = repin(directory, verify)
    with pytest.raises((b.BackupError, sqlite3.DatabaseError)):
        b.restore_isolated(directory, fixture.root / 'restore', private_storage_confirmed=True, **verify)
    assert not (fixture.root / 'restore').exists()


@pytest.mark.parametrize('field', ['expected_source', 'expected_schema', 'manifest_sha256'])
def test_expected_identity_is_required(fixture, field):
    directory, _, verify = capture(fixture)
    verify[field] = '0' * 64
    with pytest.raises(b.BackupError):
        b.verify_backup(directory, **verify)


def test_source_manifest_mismatch_fails_before_backup(fixture):
    (fixture.args['source_root'] / 'example.py').write_text('changed')
    with pytest.raises(b.BackupError):
        capture(fixture)
    assert not list(fixture.root.glob('.incomplete-*'))


def test_restore_preserves_all_records_and_blocks_replay(fixture, monkeypatch):
    directory, _, verify = capture(fixture)
    expected = rows(directory / 'state.sqlite3')
    destination = fixture.root / 'isolated'
    # A verifier must not instantiate Store, even if current constructors change later.
    with monkeypatch.context() as context:
        context.setattr(Store, '__init__', lambda *a, **kw: pytest.fail('Verifier called application startup'))
        result = b.restore_isolated(directory, destination, private_storage_confirmed=True, **verify)
    restored = destination / 'state.sqlite3'
    assert result['status'] == 'QUARANTINED' and result['automatic_replay'] is False
    assert rows(restored) == expected
    assert {'commands', 'actions', 'agent_controls', 'audit_log', 'domain_records', 'domain_reminders'} <= set(expected)
    assert any(row[4] == 'APPROVED' for row in expected['actions'])
    assert any(row[4] == 'EXECUTING' for row in expected['actions'])
    with pytest.raises(RestoreQuarantined):
        Store(restored)
    # Even a Store constructed without __init__ cannot claim work through _connect.
    cached = object.__new__(Store)
    cached.path = restored
    with pytest.raises(RestoreQuarantined):
        cached.next_queued_command()
    with pytest.raises(RestoreQuarantined):
        cached.next_approved_action()
    with pytest.raises(RestoreQuarantined):
        cached.reset_executing_actions()
    with pytest.raises(RestoreQuarantined):
        AgentControls(cached, default_registry())
    # A copy that loses the sidecar is still quarantined by database header identity.
    moved = fixture.root / 'renamed.sqlite3'
    shutil.copyfile(restored, moved)
    with pytest.raises(RestoreQuarantined):
        Store(moved)
    assert rows(restored) == expected
    assert rows(fixture.store.path) == expected
    b.verify_backup(directory, **verify)


def test_worker_startup_stops_before_gateway_or_delivery(fixture, monkeypatch):
    directory, _, verify = capture(fixture)
    destination = fixture.root / 'isolated'
    b.restore_isolated(directory, destination, private_storage_confirmed=True, **verify)
    from worker import runner
    monkeypatch.setenv('JOB_WORKER_DB', str(destination / 'state.sqlite3'))
    monkeypatch.setattr(runner, 'build_gateway', lambda: pytest.fail('Gateway must not start'))
    with pytest.raises(RestoreQuarantined):
        runner.main()


def test_malformed_sidecar_fails_closed_without_db_mutation(fixture):
    before = b.digest_file(fixture.store.path)
    marker_path(fixture.store.path).write_text('invalid-json')
    with pytest.raises(RestoreQuarantined):
        fixture.store.counts()
    assert b.digest_file(fixture.store.path) == before


def test_selected_assets_require_quiescence_and_restore_as_data(fixture):
    doc = fixture.root / 'synthetic.txt'
    doc.write_text('Synthetic provenance; no person or credential data.')
    assets = [{'path': str(doc), 'logical_path': 'documents/example.txt', 'kind': 'document'}]
    with pytest.raises(b.BackupError):
        capture(fixture, assets=assets)
    directory, _, verify = capture(fixture, assets=assets, writers_quiesced=True)
    dest = fixture.root / 'isolated'
    b.restore_isolated(directory, dest, **verify, private_storage_confirmed=True)
    assert (dest / 'assets/documents/example.txt').read_bytes() == doc.read_bytes()
    assert b.verify_backup(directory, **verify)['coverage'] == 'database-and-selected-files'


@pytest.mark.parametrize('name', ['.env', 'email-settings.json', 'cookies.json', 'credentials.txt', 'site-sessions/a.json'])
def test_known_credential_and_session_files_rejected(fixture, name):
    doc = fixture.root / name
    doc.parent.mkdir(exist_ok=True)
    doc.write_text('SYNTHETIC_ONLY')
    with pytest.raises(b.BackupError):
        capture(fixture, writers_quiesced=True, assets=[{'path': str(doc), 'logical_path': 'safe.txt', 'kind': 'configuration'}])


@pytest.mark.parametrize('name', ['../escape.txt', '/absolute.txt', 'C:/escape.txt', 'a\\b.txt', 'NUL.txt', 'a/../b.txt'])
def test_asset_traversal_rejected(fixture, name):
    doc = fixture.root / 'doc.txt'
    doc.write_text('synthetic')
    with pytest.raises(b.BackupError):
        capture(fixture, writers_quiesced=True, assets=[{'path': str(doc), 'logical_path': name, 'kind': 'document'}])


def test_reparse_detection_without_privileged_link_creation(fixture, monkeypatch):
    target = fixture.store.path
    original = Path.lstat
    def lstat(path):
        value = original(path)
        if path == target:
            return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
        return value
    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(b.BackupError):
        capture(fixture)


def test_no_overwrite_and_no_automatic_retention(fixture):
    directory, _, verify = capture(fixture)
    first_digest = b.digest_file(directory / 'state.sqlite3')
    with pytest.raises(b.BackupError):
        capture(fixture)
    second, _, _ = capture(fixture, 'second')
    assert directory.exists() and second.exists()
    assert b.digest_file(directory / 'state.sqlite3') == first_digest
    with pytest.raises(b.BackupError):
        b.restore_isolated(directory, fixture.root, **verify, private_storage_confirmed=True)


def test_private_storage_confirmation_required(fixture):
    with pytest.raises(b.BackupError):
        capture(fixture, private_storage_confirmed=False)
    directory, _, verify = capture(fixture)
    with pytest.raises(b.BackupError):
        b.restore_isolated(directory, fixture.root / 'restore', **verify)


def test_missing_asset_fails_and_does_not_create_destination(fixture):
    with pytest.raises(FileNotFoundError):
        capture(fixture, writers_quiesced=True, assets=[{'path': str(fixture.root / 'missing.txt'), 'logical_path': 'a.txt', 'kind': 'document'}])
    assert not (fixture.root / 'backup').exists()


def test_failed_restore_does_not_publish_or_change_backup(fixture, monkeypatch):
    directory, _, verify = capture(fixture)
    before = b.digest_file(directory / 'state.sqlite3')
    monkeypatch.setattr(b, '_write', lambda *a: (_ for _ in ()).throw(OSError('synthetic disk full')))
    with pytest.raises(OSError):
        b.restore_isolated(directory, fixture.root / 'restore', **verify, private_storage_confirmed=True)
    assert not (fixture.root / 'restore').exists()
    assert not list(fixture.root.glob('.incomplete-*'))
    assert b.digest_file(directory / 'state.sqlite3') == before


def test_cli_verification_outputs_metadata_only(fixture, capsys):
    directory, _, verify = capture(fixture)
    from operations.__main__ import main
    assert main(['verify', str(directory), '--manifest-sha256', verify['manifest_sha256'],
                 '--expected-source', verify['expected_source'], '--expected-schema', verify['expected_schema']]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)['status'] == 'VERIFIED'
    assert 'Synthetic' not in output and 'instruction' not in output


def test_docker_copies_guard_dependency():
    dockerfile = Path(__file__).resolve().parents[1] / 'gateway/Dockerfile'
    assert 'COPY operations ./operations' in dockerfile.read_text()


def test_interrupt_during_sqlite_copy_cleans_partial_database(fixture, monkeypatch):
    original = b._connect_ro
    class InterruptedConnection:
        def __init__(self, connection):
            self.connection = connection
        def backup(self, destination, **kwargs):
            def interrupt(*args):
                raise KeyboardInterrupt()
            self.connection.backup(destination, **(kwargs | {'progress': interrupt}))
        def close(self):
            self.connection.close()
    monkeypatch.setattr(b, '_connect_ro', lambda *a, **kw: InterruptedConnection(original(*a, **kw)))
    with pytest.raises(KeyboardInterrupt):
        capture(fixture)
    assert not list(fixture.root.glob('.incomplete-*'))
    assert not (fixture.root / 'backup').exists()


def test_version_metadata_preserved_except_explicit_restore_quarantine(fixture):
    with fixture.store._connect() as con:
        con.execute('PRAGMA user_version=17')
        con.execute('PRAGMA application_id=1234')
    directory, _, verify = capture(fixture)
    manifest = b.verify_backup(directory, **verify)
    assert manifest['database']['user_version'] == 17
    assert manifest['database']['application_id'] == 1234
    dest = fixture.root / 'restored'
    b.restore_isolated(directory, dest, **verify, private_storage_confirmed=True)
    restored = b.database_identity(dest / 'state.sqlite3')
    assert restored['user_version'] == 17
    assert restored['application_id'] == QUARANTINE_APPLICATION_ID
    assert json.loads((dest / 'restore-receipt.json').read_text())['original_application_id'] == 1234
    assert rows(dest / 'state.sqlite3') == rows(directory / 'state.sqlite3')


def test_asset_mutation_during_copy_rejected(fixture, monkeypatch):
    document = fixture.root / 'doc.txt'
    document.write_text('before')
    original = shutil.copyfileobj
    def mutate(src, dest, *args, **kwargs):
        original(src, dest, *args, **kwargs)
        if Path(src.name) == document:
            document.write_text('after-change')
    monkeypatch.setattr(shutil, 'copyfileobj', mutate)
    with pytest.raises(b.BackupError, match='changed'):
        capture(fixture, writers_quiesced=True, assets=[{'path': str(document), 'logical_path': 'doc.txt', 'kind': 'document'}])
    assert not list(fixture.root.glob('.incomplete-*'))


def test_restore_verification_never_starts_network_or_subprocess(fixture, monkeypatch):
    directory, _, verify = capture(fixture)
    import socket
    import subprocess
    def denied(*a, **kw):
        pytest.fail('Offline restore attempted an external operation')
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)
    b.restore_isolated(directory, fixture.root / 'isolated', **verify, private_storage_confirmed=True)


def test_hard_interruption_after_complete_marker_is_still_unpublished(fixture):
    directory, _, verify = capture(fixture)
    abandoned = fixture.root / '.incomplete-synthetic-interruption'
    directory.rename(abandoned)
    with pytest.raises(b.BackupError, match='Staging'):
        b.verify_backup(abandoned, **verify)
    with pytest.raises(b.BackupError):
        b.restore_isolated(abandoned, fixture.root / 'restore', **verify, private_storage_confirmed=True)


@pytest.mark.parametrize('replacement', [[], {'format_version': 1, 'database': []}])
def test_malformed_pinned_manifest_fails_cleanly(fixture, replacement, capsys):
    directory, _, verify = capture(fixture)
    (directory / 'manifest.json').write_text(json.dumps(replacement))
    verify = repin(directory, verify)
    from operations.__main__ import main
    assert main(['verify', str(directory), '--manifest-sha256', verify['manifest_sha256'],
                 '--expected-source', verify['expected_source'], '--expected-schema', verify['expected_schema']]) == 1
    assert json.loads(capsys.readouterr().out)['error_type'] == 'BackupError'


def test_wal_backup_does_not_change_source_schema_or_controls(fixture):
    before = rows(fixture.store.path)
    with fixture.store._connect() as con:
        identity = tuple(con.execute('PRAGMA ' + name).fetchone()[0]
                         for name in ('schema_version', 'user_version', 'application_id'))
    capture(fixture)
    assert rows(fixture.store.path) == before
    with fixture.store._connect() as con:
        assert identity == tuple(con.execute('PRAGMA ' + name).fetchone()[0]
                                 for name in ('schema_version', 'user_version', 'application_id'))


def test_restore_rechecks_payload_after_initial_verification(fixture, monkeypatch):
    directory, _, verify = capture(fixture)
    original = b.verify_backup
    def mutate_after_verification(*a, **kw):
        manifest = original(*a, **kw)
        db = directory / 'state.sqlite3'
        db.write_bytes(db.read_bytes() + b'changed')
        return manifest
    monkeypatch.setattr(b, 'verify_backup', mutate_after_verification)
    with pytest.raises(b.BackupError, match='changed'):
        b.restore_isolated(directory, fixture.root / 'restore', **verify, private_storage_confirmed=True)
    assert not (fixture.root / 'restore').exists()
