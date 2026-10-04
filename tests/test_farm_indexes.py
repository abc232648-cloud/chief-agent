import json
import sqlite3
import uuid
import pytest
from database import farm_indexes
from domains.farming import journal
from tests.checkpoint_f_fixture import recovery_point, PASSWORD
from tests.test_farm_journal import record
from identity.service import IdentityService


def upgrade(dashboard):
    d=dashboard
    args=recovery_point(d.store,d.store.path.parent)
    assert farm_indexes.migrate_isolated(d.store,**args)=='APPLIED'
    return args


def test_populated_preservation_idempotency_and_rollback_readability(dashboard):
    d=dashboard;p=d.credentials['principal']
    journal.append(d.store,p,record())
    with d.store._connect() as con:before=farm_indexes.legacy_digest(con)
    previous=journal.overview(d.store,p)
    args=upgrade(d)
    assert farm_indexes.migrate_isolated(d.store,**args)=='ALREADY_APPLIED'
    assert journal.overview(d.store,p)==previous
    with d.store._connect() as con:
        assert before==farm_indexes.legacy_digest(con)
        # Old software can still read exact rows; indexes don't rewrite data.
        assert len(journal.read_rows(con))==1
        assert farm_indexes.schema_ready(con)


def test_failed_migration_rolls_back_every_index_and_can_retry(dashboard,monkeypatch):
    d=dashboard;args=recovery_point(d.store,d.store.path.parent)
    original=farm_indexes.DDL.copy()
    with monkeypatch.context() as patch:
        patch.setitem(farm_indexes.DDL,'farm_journal_event','THIS IS NOT SQL')
        with pytest.raises(sqlite3.OperationalError):farm_indexes.migrate_isolated(d.store,**args)
    with d.store._connect() as con:
        assert not set(original)&{r[0] for r in con.execute('SELECT name FROM sqlite_master')}
    assert farm_indexes.migrate_isolated(d.store,**args)=='APPLIED'


def test_changed_fixture_and_unverified_restore_rejected(dashboard):
    d=dashboard;args=recovery_point(d.store,d.store.path.parent)
    journal.append(d.store,d.credentials['principal'],record())
    with pytest.raises(ValueError,match='changed after backup'):farm_indexes.migrate_isolated(d.store,**args)
    args=recovery_point(d.store,d.store.path.parent)
    (args['restore_directory']/'restore-receipt.json').write_text('{}')
    with pytest.raises(ValueError,match='restore required'):farm_indexes.migrate_isolated(d.store,**args)


def test_indexed_and_legacy_correction_visibility_equivalent(dashboard):
    d=dashboard;p=d.credentials['principal'];s=IdentityService(d.store)
    s.create_user(p,'index-worker',PASSWORD,'Worker',('farming',));_,worker=s.login('index-worker',PASSWORD)
    opening=record(kind='feed_opening');journal.append(d.store,p,opening)
    movement=record(kind='feed_received',quantity='2.500');journal.append(d.store,worker,movement)
    correction={**movement,'event_id':str(uuid.uuid4()),'corrects':movement['event_id'],'reason':'Correct quantity','quantity':'3.125'}
    journal.append(d.store,p,correction)
    before={who.id:journal.overview(d.store,who) for who in (p,worker)}
    upgrade(d)
    assert before=={who.id:journal.overview(d.store,who) for who in (p,worker)}
    correction2={**correction,'event_id':str(uuid.uuid4()),'corrects':correction['event_id'],'quantity':'4.125'}
    journal.append(d.store,p,correction2)
    own=journal.overview(d.store,worker)
    assert own['record_count']==3 and not own['balances']
    assert sum(r['is_current'] for r in own['records'])==1
    assert journal.append(d.store,p,correction2)['status']=='ALREADY_RECORDED'
    with pytest.raises(ValueError):journal.append(d.store,p,{**correction2,'event_id':str(uuid.uuid4())})


def test_growth_beyond_pilot_limit_exact_balance_and_pagination(dashboard):
    d=dashboard;p=d.credentials['principal'];opening=record(kind='feed_opening',quantity='100.000')
    journal.append(d.store,p,opening)
    # Populate BEFORE backup/migration; exercise a real populated upgrade.
    template=journal.overview(d.store,p)['records'][0];template.pop('is_current')
    def generated():
        for index in range(12000):
            r={**template,'payload':{**opening,'kind':'feed_received','event_id':'scale-event-'+str(index),'quantity':'0.001'}}
            yield ('farming',journal.KIND,json.dumps(r),1)
    with d.store._connect() as con:con.executemany('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',generated())
    with pytest.raises(ValueError,match='capacity exceeded'):journal.overview(d.store,p)
    upgrade(d)
    result=journal.overview(d.store,p)
    assert result['record_count']==12001 and len(result['records'])==100 and result['next_offset']==100
    assert result['balances'][0]['recorded_balance']=='112.000'
    assert len(journal.overview(d.store,p,offset=12000)['records'])==1
    movement=record(kind='feed_used',quantity='0.125')
    assert journal.append(d.store,p,movement)['status']=='RECORDED'
    assert journal.overview(d.store,p)['balances'][0]['recorded_balance']=='111.875'
    with d.store._connect() as con:
        plan=' '.join(str(tuple(r)) for r in con.execute("EXPLAIN QUERY PLAN SELECT data_json FROM domain_records WHERE domain='farming' AND kind='poultry_journal_v1' AND json_extract(data_json,'$.payload.event_id')=?",(movement['event_id'],)))
    assert 'farm_journal_event' in plan
