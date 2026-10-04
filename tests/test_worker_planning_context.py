import json
from types import SimpleNamespace
import pytest
from database.store import Store
from worker.command_processor import CommandProcessor

class Gateway:
    def __init__(self,actions): self.actions=actions; self.request=None
    def generate(self,request):
        self.request=request
        return SimpleNamespace(text=json.dumps({'summary':'Test plan','actions':self.actions}))

def test_empty_plan_is_not_reported_as_executed(tmp_path):
    store=Store(tmp_path/'db.sqlite');cid=store.queue_command('Test')
    CommandProcessor(store,Gateway([])).process_command(cid,'Test')
    assert store.commands()[0]['status']=='NO_ACTION'
    assert 'Test plan' in store.worker()['message']

def test_plan_receives_only_existing_enabled_context(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    enabled=store.add_profile_link({'label':'Profile','url':'https://example.test/me'})
    disabled=store.add_profile_link({'label':'Disabled','url':'https://example.test/disabled','enabled':False})
    gateway=Gateway([]);processor=CommandProcessor(store,gateway)
    processor._plan('Check my profiles')
    envelope=json.loads(gateway.request.user)
    assert envelope['instruction']=='Check my profiles'
    assert [p['id'] for p in envelope['context']['enabled_profiles']]==[enabled]
    expected=[{'name':s.name,'urls':list(s.start_urls)} for s in processor.browser_worker.sources if s.enabled and s.read_only]
    assert envelope['context']['configured_sources']==expected
    assert 'SMTP_PASSWORD' not in gateway.request.user and 'API_KEY' not in gateway.request.user
    assert gateway.request.max_tokens==512

@pytest.mark.parametrize('action,payload',[('discover_jobs',{}),('discover_jobs',{'urls':[]}),('discover_jobs',{'urls':['dynamic_from_discovery']}),('read_job_listing',{}),('screen_scam',{'job_ids':'dynamic_from_discovery'})])
def test_invalid_or_unimplemented_action_cannot_complete(tmp_path,action,payload):
    store=Store(tmp_path/'db.sqlite');gateway=Gateway([{'action':action,'payload':payload}])
    cid=store.queue_command('Test')
    result=CommandProcessor(store,gateway).process_command(cid,'Test')
    assert store.commands()[0]['status']=='FAILED'
    assert store.worker()['status']=='FAILED'
    assert result['results'][0]['result']['status'] in {'FAILED','NOT_EXECUTED'}

@pytest.mark.parametrize('field',['errors','quarantined'])
def test_discovery_failure_is_not_completed(tmp_path,field):
    store=Store(tmp_path/'db.sqlite');gateway=Gateway([{'action':'discover_jobs','payload':{'urls':['https://example.test/jobs']}}])
    class Browser:
        def discover(self,*args):return {'status':'COMPLETED',field:[{'reason':'fixture failure'}]}
    cid=store.queue_command('Test')
    CommandProcessor(store,gateway,browser_worker=Browser()).process_command(cid,'Test')
    assert store.commands()[0]['status']=='FAILED'

@pytest.mark.parametrize('actions',[[None],[{'action':'discover_jobs','payload':[]}],[{'action':7}]])
def test_malformed_plan_fails_explicitly(tmp_path,actions):
    store=Store(tmp_path/'db.sqlite');cid=store.queue_command('Test')
    with pytest.raises(ValueError):CommandProcessor(store,Gateway(actions)).process_command(cid,'Test')
    assert store.commands()[0]['status']=='FAILED'
