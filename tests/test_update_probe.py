import json

from database.store import Store
from deployment.schema import fingerprint
from deployment.update_probe import main
from tests.checkpoint_f_fixture import secure_store
from tests.test_operational_backup import rows


def test_real_probe_checks_populated_isolated_database_without_mutation(tmp_path, monkeypatch, capsys):
    store = Store(tmp_path / 'synthetic.db')
    from control.notifications import initialize
    from browser.site_access import SiteAccess
    initialize(store)
    SiteAccess(store)
    secure_store(store)  # Includes B.5 backup/restore before each isolated migration.
    store.queue_command('Synthetic queued work; must not execute')
    action = store.add_action('Synthetic', 'fixture')
    store.resolve_action(action, 'APPROVED')
    with store._connect() as con:
        schema = fingerprint(con)
    config = tmp_path / 'instance.json'
    config.write_text(json.dumps(dict(mode='ISOLATED_DEVELOPMENT', state_root=str(tmp_path),
        database=str(store.path), schema_sha256=schema)), encoding='utf-8')
    # configure() is exercised; contain its environment changes within the test.
    import os
    monkeypatch.setattr(os, 'environ', dict(os.environ))
    for key in ('JOB_WORKER_DB', 'CHIEF_STATE_ROOT', 'DASHBOARD_HOST'):
        os.environ.pop(key, None)
    before = rows(store.path)
    assert main(['--config', str(config), '--schema', schema]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'READY'
    assert rows(store.path) == before
    assert main(['--config', str(config), '--schema', '0'*64]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output == {'status': 'NOT_READY', 'external_actions': False}
    assert rows(store.path) == before
