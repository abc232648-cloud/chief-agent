import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from database.store import Store
from update_center.history import record,read,PREFIX

@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path/'test.db')
    with s._connect() as con:con.execute('CREATE TABLE control_state(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    return s

def event(n=1):return dict(event_id=f'{n:032x}',outcome='DEPLOYED',source_sha256='a'*64,evidence_sha256='b'*64)

def test_idempotency_and_conflict(store):
    first=record(store,event());assert record(store,event())==first
    with pytest.raises(ValueError):record(store,dict(event(),outcome='FAILED'))
    assert read(store)['events']==[first]

def test_concurrent_retry_is_one_record(store):
    with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(lambda _:record(store,event()),range(8)))
    assert all(r==rows[0] for r in rows)
    assert len(read(store)['events'])==1

def test_history_is_paged_and_no_bad_record_is_success(store):
    for n in range(1,23):record(store,event(n))
    page=read(store);assert len(page['events'])==20 and page['has_more']
    assert len(read(store,offset=20)['events'])==2
    with store._connect() as con:con.execute('INSERT INTO control_state VALUES(?,?)',(PREFIX+'f'*32,'private-invalid-json'))
    page=read(store);assert page['invalid_records']==1 and 'private-invalid-json' not in str(page)

def test_no_history_schema_is_read_only(tmp_path):
    s=Store(tmp_path/'test.db');assert read(s)['events']==[]
    with s._connect() as con:assert con.execute("SELECT 1 FROM sqlite_master WHERE name='control_state'").fetchone() is None

@pytest.mark.parametrize('change',[{'outcome':'AUTOMATICALLY_APPROVED'},{'source_sha256':'wrong'},{'event_id':'../path'},{'extra':'private'}])
def test_invalid_event_rejected(store,change):
    with pytest.raises(ValueError):record(store,dict(event(),**change))
    assert read(store)['events']==[]

def test_operator_cli_writes_existing_schema_only(store,monkeypatch,capsys):
    from types import SimpleNamespace
    from application.update_history import main
    monkeypatch.setattr('deployment.launch.configure',lambda _:SimpleNamespace(database=store.path))
    with store._connect() as con:before=con.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall()
    e=event();args=['--config','synthetic','--event-id',e['event_id'],'--outcome',e['outcome'],'--source-sha256',e['source_sha256'],'--evidence-sha256',e['evidence_sha256']]
    assert main(args)==0
    assert json.loads(capsys.readouterr().out)['installation_changed'] is False
    with store._connect() as con:assert [tuple(r) for r in con.execute('SELECT sql FROM sqlite_master ORDER BY name')]==[tuple(r) for r in before]
    assert read(store)['events'][0]['source_sha256']==e['source_sha256']


def test_update_history_browser_labels_records_as_observations(dashboard):
    from playwright.sync_api import sync_playwright,expect
    from tests.browser_navigation import navigate
    record(dashboard.store,event())
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url);navigate(page,'updates')
            expect(page.locator('#updatesContent')).to_contain_text('Operator-recorded activity')
            expect(page.locator('#updatesContent')).to_contain_text('DEPLOYED')
            expect(page.locator('#updatesContent')).to_contain_text('a'*64)
            assert page.locator('#updatesContent button').count()==0
        finally:browser.close()
