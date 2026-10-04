"""Every E migration fixture includes a freshly verified populated D recovery point."""
import pytest
from tests.checkpoint_d_fixture import prepare_d
from database.evidence_migrations import migrate_isolated as migrate_d
from database.execution_migrations import migrate_isolated,legacy_digest
from domains.jobs.evidence_adapter import JobEvidence,job_scope
from domains.jobs.ledger_adapter import JobLedger
from operations.backup import create_backup,restore_isolated


def prepare_e(root):
    f=prepare_d(root)
    assert migrate_d(f.store,**f.args)=='APPLIED'
    with job_scope():
        adapter=JobEvidence(f.store)
        f.evidence_id=adapter.adapt(f.fact_ids[1],truth='DOCUMENTED')
        adapter.shared.quality(f.evidence_id,persist=True)
        JobLedger(f.store).ledger.append(event_key='d-fixture',correlation_id='d-fixture',action='read_job_listing',phase='OUTCOME',outcome='RECORDED',rationale='SYNTHETIC',risk='READ',policy_decision='ALLOW',evidence_ids=(f.evidence_id,))
    with f.store._connect() as con:f.before=legacy_digest(con)
    receipt=create_backup(f.store.path,root/'d-backup',source_root=root/'synthetic-release',source_manifest=root/'release.json',private_storage_confirmed=True)
    pins=dict(manifest_sha256=receipt['manifest_sha256'],expected_source=receipt['source_tree_sha256'],expected_schema=receipt['schema_sha256'])
    restore_isolated(root/'d-backup',root/'d-restore',**pins,private_storage_confirmed=True)
    f.args=dict(isolated_root=root,backup_directory=root/'d-backup',restore_directory=root/'d-restore',**pins)
    return f


@pytest.fixture
def d_base(tmp_path):return prepare_e(tmp_path)


@pytest.fixture
def e_fixture(d_base):
    assert migrate_isolated(d_base.store,**d_base.args)=='APPLIED'
    with job_scope():yield d_base
