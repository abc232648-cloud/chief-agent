"""Every D fixture uses actual B.5 backup/restore both before C and before D."""
from types import SimpleNamespace
import pytest
from tests.checkpoint_c_fixture import prepare
from database.migrations import migrate_isolated as migrate_c
from database.evidence_migrations import migrate_isolated, legacy_digest
from operations.backup import create_backup, restore_isolated
from domains.jobs.evidence_adapter import job_scope


def prepare_d(root):
    fixture = prepare(root)
    assert migrate_c(fixture.store, **fixture.args) == 'APPLIED'
    store = fixture.store
    fixture.fact_ids = [store.add_candidate_fact({'text':'Nmap','status':s,'source_type':'synthetic','source_id':'fixture'}) for s in ('PROPOSED','USER_CONFIRMED','REVOKED')]
    store.add_job({'id':'job-fixture','title':'SOC Analyst'})
    store.add_application('app-fixture','job-fixture',draft={'claims':[{'fact_id':fixture.fact_ids[1]}]})
    fixture.snapshot = store.add_application_snapshot({'application_id':'app-fixture','stage':'DRAFT','cv_snapshot':{'synthetic':'preserve'},'cover_letter_text':'Synthetic preserve'})
    fixture.event = store.add_application_event('app-fixture','DRAFT_CREATED')
    with store._connect() as con:
        con.execute("INSERT INTO fact_history(fact_id,text,status) VALUES(?, 'Nmap', 'USER_CONFIRMED')", (fixture.fact_ids[1],))
        fixture.before = legacy_digest(con)
    receipt = create_backup(store.path,root/'c-backup',source_root=root/'synthetic-release',source_manifest=root/'release.json',private_storage_confirmed=True)
    pins = {k:receipt[k] for k in ('manifest_sha256',)}
    pins.update(expected_source=receipt['source_tree_sha256'],expected_schema=receipt['schema_sha256'])
    restore_isolated(root/'c-backup',root/'c-restore',**pins,private_storage_confirmed=True)
    fixture.args = dict(isolated_root=root,backup_directory=root/'c-backup',restore_directory=root/'c-restore',**pins)
    return fixture


@pytest.fixture
def c_fixture(tmp_path):
    return prepare_d(tmp_path)


@pytest.fixture
def d_fixture(c_fixture):
    assert migrate_isolated(c_fixture.store, **c_fixture.args) == 'APPLIED'
    with job_scope():
        yield c_fixture
