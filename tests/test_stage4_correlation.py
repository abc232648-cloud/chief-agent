from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from operations.correlation import scope,references
from database.store import Store
from database.store_extensions import add_audit


def test_context_isolated_across_threads_and_exceptions():
    def work(n):
        with scope(command_id=n):
            assert references()=={'command_id':n}
            with scope(action_id=n):assert references()=={'command_id':n,'action_id':n}
            return references()
    with ThreadPoolExecutor(4) as pool:assert list(pool.map(work,range(1,9)))==[{'command_id':n} for n in range(1,9)]
    with pytest.raises(RuntimeError):
        with scope(request_id='a'*32):raise RuntimeError()
    assert references()=={}


def test_audit_links_request_work_and_ledger_without_payload(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    with scope(request_id='a'*32,command_id=1,ledger_correlation_id='b'*32):
        add_audit(store,'command','Synthetic reference',data={'api_key':'synthetic-must-not-appear'})
    row=store.audit()[0];data=json.loads(row['data_json'])
    assert data['request_id']=='a'*32 and data['command_id']==1 and data['ledger_correlation_id']=='b'*32
    assert 'synthetic-must-not-appear' not in row['data_json']


@pytest.mark.parametrize('values',[{'request_id':'user-controlled'},{'command_id':True},{'action_id':0},{'secret':'x'}])
def test_untrusted_correlation_rejected(values):
    with pytest.raises(ValueError):
        with scope(**values):pass
    assert references()=={}


def test_real_http_request_id_is_generated_and_linked_to_queue(dashboard):
    import urllib.request
    request=urllib.request.Request(dashboard.url+'/api/command',data=json.dumps({'instruction':'synthetic inspection'}).encode(),headers={'Content-Type':'application/json','X-Request-ID':'untrusted'})
    with urllib.request.urlopen(request) as response:
        request_id=response.headers['X-Request-ID'];result=json.load(response)
    assert len(request_id)==32 and request_id!='untrusted'
    records=[json.loads(row['data_json']) for row in dashboard.store.audit() if row['action']=='Dashboard command queued']
    assert records==[{'command_id':result['command_id'],'request_id':request_id,'human_id':dashboard.credentials['principal'].id,'human_session_id':'[REDACTED]'}]
    from domains.jobs.ledger_adapter import JobCommandProcessor
    from tests.test_command_processor import FakeGateway
    processor=JobCommandProcessor(dashboard.store,FakeGateway('{"summary":"Synthetic no action","actions":[]}'))
    processor.process_command(result['command_id'],'synthetic inspection')
    entries=[json.loads(row['data_json']) for row in dashboard.store.audit() if row['category']=='command']
    worker=[row for row in entries if row.get('ledger_correlation_id')]
    assert worker and all(row['command_id']==result['command_id'] and row['ledger_correlation_id']==processor.ledger_observer.last_correlation for row in worker)
    assert references()=={}
