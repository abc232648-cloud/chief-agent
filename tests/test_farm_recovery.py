import base64
import json
import sqlite3
import uuid
from domains.farming import setup,journal,bookkeeping,staff,photos
from tests.checkpoint_f_fixture import recovery_point
from tests.test_farm_setup import entity
from tests.test_farm_journal import record
from tests.test_farm_bookkeeping import entry
from tests.test_farm_staff import event
from tests.test_farm_photos import png


def test_farm_records_photos_and_audit_survive_quarantined_backup_restore(dashboard):
    d=dashboard;p=d.credentials['principal']
    with d.store._connect() as con:
        schema=list(map(tuple,con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))
    e=entity();setup.append(d.store,p,e)
    journal.append(d.store,p,record(entity_id=e['entity_id'],location=e['name']))
    bookkeeping.append(d.store,p,entry())
    incident=event();staff.append(d.store,p,incident)
    photos.append(d.store,p,{'event_id':str(uuid.uuid4()),'work_id':incident['event_id'],'image_base64':base64.b64encode(png()).decode()})
    with d.store._connect() as con:
        assert schema==list(map(tuple,con.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))
        expected={table:list(map(tuple,con.execute('SELECT * FROM '+table+' ORDER BY rowid'))) for table in ('domain_records','human_security_events','actions','agent_controls')}
    args=recovery_point(d.store,d.store.path.parent)
    restored=args['restore_directory']/'state.sqlite3'
    with sqlite3.connect(restored) as con:
        actual={table:list(map(tuple,con.execute('SELECT * FROM '+table+' ORDER BY rowid'))) for table in expected}
    assert actual==expected
    receipt=json.loads((args['restore_directory']/'restore-receipt.json').read_text())
    assert receipt['status']=='QUARANTINED' and receipt['external_actions']=='DISABLED_NO_AUTOMATIC_PROMOTION'
