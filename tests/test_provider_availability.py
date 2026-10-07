import pytest
from gateway.availability import AvailableProvider
from gateway.models import AIRequest,AIResponse
from gateway.errors import ProviderUnavailable,InvalidProviderResponse
from database.store import Store

@pytest.fixture(autouse=True)
def clean_keys(monkeypatch):
    for name in ('GROQ_API_KEY','MISTRAL_API_KEY','GROQ_API_KEY_REF','MISTRAL_API_KEY_REF'):
        monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv('FREE_ONLY','TRUE');monkeypatch.setenv('MISTRAL_PAID_ALLOWED','FALSE')

def test_missing_keys_notify_once_across_reconstruction(tmp_path):
    from gateway.main import build_gateway
    store=Store(tmp_path/'test.db')
    for _ in range(2):
        gateway=build_gateway(store=store)
        with pytest.raises(Exception,match='STOP'):gateway.generate(AIRequest('s','u'))
    assert len(store.notifications())==2
    assert all('MISSING_KEY' in n['body'] for n in store.notifications())

def test_unreadable_reference_does_not_construct_client(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY_REF','missing')
    def forbidden(*a):pytest.fail('No client construction')
    p=AvailableProvider(forbidden,'q','GROQ_API_KEY','Groq')
    p.inspect()
    assert p.status=='KEY_UNAVAILABLE'
    with pytest.raises(ProviderUnavailable):p.generate(AIRequest('s','u'))

@pytest.mark.parametrize('status,expected',[(401,'AUTH_REJECTED'),(403,'ACCESS_DENIED'),(404,'MODEL_UNAVAILABLE'),(429,'RATE_LIMITED'),(503,'UNAVAILABLE')])
def test_failure_classification_redaction_and_recovery(monkeypatch,tmp_path,status,expected):
    monkeypatch.setenv('GROQ_API_KEY','PRIVATE_TEST_KEY');store=Store(tmp_path/'test.db')
    class Failure(Exception):status_code=status
    class Client:
        failing=True
        def generate(self,request):
            if self.failing:raise Failure('PRIVATE_TEST_KEY private prompt')
            return AIResponse('qwen','q','ok')
    client=Client();p=AvailableProvider(lambda *a:client,'q','GROQ_API_KEY','Groq',store=store)
    with pytest.raises(ProviderUnavailable) as exc:p.generate(AIRequest('s','u'))
    assert p.status==expected and 'PRIVATE_TEST_KEY' not in str(exc.value)
    assert 'PRIVATE_TEST_KEY' not in str(store.notifications())
    client.failing=False
    assert p.generate(AIRequest('s','u')).text=='ok'
    assert p.status=='READY' and len(store.notifications())==2

def test_invalid_response_remains_nonfallback_failure(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY','synthetic')
    class Client:
        def generate(self,r):raise InvalidProviderResponse('Invalid response')
    p=AvailableProvider(lambda *a:Client(),'q','GROQ_API_KEY','Groq')
    with pytest.raises(InvalidProviderResponse):p.generate(AIRequest('s','u'))

def test_worker_ready_without_any_model_keys(tmp_path,monkeypatch):
    from tests.checkpoint_f_fixture import secure_store
    from worker import runner
    from threading import Event
    store=Store(tmp_path/'test.db');secure_store(store)
    stop=Event();stop.set();ready=[]
    assert runner._owned_main(store,stop,lambda:ready.append(True))==0
    assert ready==[True]

def test_disabled_model_does_not_emit_missing_key_notice(tmp_path):
    from tests.checkpoint_f_fixture import secure_store
    from gateway.main import build_gateway
    store=Store(tmp_path/'test.db');secure_store(store)
    gateway=build_gateway(store=store)
    gateway.registry.set_global_state('jobs.mistral','DISABLED',actor='operator')
    with store._connect() as con:con.execute('DELETE FROM notifications')
    build_gateway(store=store)
    assert [r['title'] for r in store.notifications()]==['Groq model connection']


def test_concurrent_missing_key_notice_is_deduplicated(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from control.notifications import initialize
    store=Store(tmp_path/'test.db');initialize(store)
    def observe(_):
        p=AvailableProvider(None,'q','GROQ_API_KEY','Groq',store=store);p.inspect()
    with ThreadPoolExecutor(max_workers=4) as executor:list(executor.map(observe,range(8)))
    assert len(store.notifications())==1


def test_only_available_assigned_provider_can_run(monkeypatch):
    from gateway import main
    monkeypatch.setenv('GROQ_API_KEY','synthetic')
    class Client:
        def __init__(self,key,model):self.model=model
        def generate(self,r):return AIResponse('qwen',self.model,'ok')
    monkeypatch.setattr(main,'QwenFreeProvider',Client)
    monkeypatch.setattr(main,'MistralFreeProvider',lambda *a:pytest.fail('Missing provider constructed'))
    assert main.build_gateway().generate(AIRequest('s','u')).text=='ok'

def test_models_view_only_exposes_fixed_observations(tmp_path,monkeypatch):
    from tests.checkpoint_f_fixture import secure_store
    from application.ui_views import system_view
    store=Store(tmp_path/'test.db');secure_store(store)
    monkeypatch.setenv('CHIEF_STATE_ROOT',str(tmp_path))
    store.add_notification('Groq model connection','PRIVATE_UNTRUSTED: secret',domain='jobs')
    view=system_view(store,'models')
    assert view['provider_observations'][0]['status']=='NOT_OBSERVED'
    assert 'secret' not in str(view['provider_observations'])

@pytest.mark.parametrize('http_status,expected',[(401,'AUTH_REJECTED'),(429,'RATE_LIMITED')])
def test_farm_failure_is_scoped_and_redacted(tmp_path,monkeypatch,http_status,expected):
    import urllib.error
    from types import SimpleNamespace
    from domains.farming import live_ai
    monkeypatch.setenv('CHIEF_STATE_ROOT',str(tmp_path))
    store=Store(tmp_path/'test.db')
    config={'registration':'synthetic','model':'qwen/synthetic'}
    monkeypatch.setattr(live_ai.ModelSetup,'_record',lambda *a:(tmp_path,{'provider':'groq','provider_model':config['model'],'cost':'FREE','backend':'synthetic'}))
    monkeypatch.setattr(live_ai.imported,'resolve',lambda *a:'PRIVATE_KEY')
    def fail(*a,**kw):raise urllib.error.HTTPError('https://synthetic.invalid',http_status,'PRIVATE_KEY',{},None)
    monkeypatch.setattr(live_ai.urllib.request,'build_opener',lambda *a:SimpleNamespace(open=fail))
    with pytest.raises(ProviderUnavailable):live_ai.Transport(config,store=store).generate(AIRequest('s','u'))
    note=store.notifications()[0]
    assert note['domain']=='farming' and expected in note['body']
    assert 'PRIVATE_KEY' not in str(note)


def test_rotated_reference_is_resolved_again_and_store_failure_is_nonfatal(tmp_path,monkeypatch):
    import gateway.availability as module
    keys=iter(['first','second']);seen=[]
    monkeypatch.setattr(module,'resolve_configured',lambda *a,**kw:next(keys))
    class BrokenStore:
        def _connect(self):raise RuntimeError('private database detail')
    class Client:
        def generate(self,r):return AIResponse('qwen','q','ok')
    def factory(key,model):seen.append(key);return Client()
    p=AvailableProvider(factory,'q','GROQ_API_KEY','Groq',store=BrokenStore())
    assert p.generate(AIRequest('s','u')).text=='ok'
    assert p.generate(AIRequest('s','u')).text=='ok'
    assert seen==['first','second']
