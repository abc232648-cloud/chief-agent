from dataclasses import replace
import pytest
from tests.checkpoint_e_fixture import d_base,e_fixture
from model_registry.contracts import Provider,Model,Assignment,InstallationPolicy,Cost,ModelState
from model_registry.registry import ModelRegistry
from model_registry.routing import ModelRouter,legacy_job_router
from gateway.router import FreeOnlyGateway
from gateway.models import AIRequest,AIResponse
from gateway.errors import GatewayError,ProviderUnavailable,InvalidProviderResponse
from security.permissions import worker_context
from domains.contracts import AgentDefinition


class Fake:
    def __init__(self,provider,model,result='ok'):
        self.provider,self.model,self.result=provider,model,result;self.calls=[]
    def generate(self,request):
        self.calls.append(request)
        if isinstance(self.result,BaseException):raise self.result
        if callable(self.result):return self.result(request)
        return AIResponse(self.provider,self.model,self.result)


def registry(store=None):
    return ModelRegistry((Provider('synthetic'),),(Model('one','synthetic','one',Cost.FREE),Model('two','synthetic','two',Cost.FREE),Model('paid','synthetic','paid',Cost.PAID),Model('unknown','synthetic','unknown',Cost.UNKNOWN)),store=store)


@pytest.mark.parametrize('primary',['ok','unavailable','invalid','error'])
@pytest.mark.parametrize('fallback',['ok','unavailable','invalid','error'])
def test_exact_legacy_routing_matrix(primary,fallback):
    def outcome(kind):
        return {'ok':'ok','unavailable':ProviderUnavailable('synthetic'),'invalid':InvalidProviderResponse('synthetic'),'error':RuntimeError('synthetic')}[kind]
    a,b=Fake('qwen','q',outcome(primary)),Fake('mistral','m',outcome(fallback))
    c,d=Fake('qwen','q',outcome(primary)),Fake('mistral','m',outcome(fallback))
    old=FreeOnlyGateway(a,b);new=legacy_job_router(c,d)
    request=AIRequest('synthetic system','synthetic user',.2,7)
    def result(gateway):
        try:return gateway.generate(request)
        except Exception as exc:return type(exc)
    assert result(old)==result(new)
    assert a.calls==c.calls and b.calls==d.calls


@pytest.mark.parametrize('state',['DISABLED','SHADOW'])
def test_global_state_overrides_stale_assignment(e_fixture,state):
    reg=registry(e_fixture.store);assignment=Assignment('jobs','jobs-worker',('one','two'))
    reg.assign(assignment,capability='jobs.execute')
    reg.set_global_state('one',state,actor='test-operator')
    a,b=Fake('synthetic','one'),Fake('synthetic','two')
    router=ModelRouter(reg,assignment,{'one':a,'two':b},capability='jobs.execute')
    assert router.generate(AIRequest('s','u')).model=='two' and not a.calls
    reg.set_global_state('two','DISABLED',actor='test-operator')
    with pytest.raises(GatewayError):router.generate(AIRequest('s','u'))
    assert len(b.calls)==1


def test_domain_agent_assignments_are_isolated(e_fixture):
    reg=registry(e_fixture.store)
    job=Assignment('jobs','jobs-worker',('one',));other=Assignment('other','other-worker',('one',))
    reg.assign(job,capability='jobs.execute')
    with worker_context(AgentDefinition('other-worker','other',frozenset({'other.read'}))):
        reg.assign(replace(other,models=('two',)),capability='other.read')
        with pytest.raises(PermissionError):reg.assign(job,capability='other.read')
        with pytest.raises(PermissionError):ModelRouter(reg,job,{},capability='jobs.execute').generate(AIRequest('s','u'))
    assert reg.assignment(job)==job and reg.assignment(other).models==('two',)


@pytest.mark.parametrize('identity',['paid','unknown'])
def test_no_paid_or_unknown_fallback(e_fixture,identity):
    reg=registry(e_fixture.store);a=Assignment('jobs','jobs-worker',('one',identity))
    primary=Fake('synthetic','one',ProviderUnavailable());fallback=Fake('synthetic',identity)
    with pytest.raises(GatewayError):ModelRouter(reg,a,{'one':primary,identity:fallback},capability='jobs.execute').generate(AIRequest('s','u'))
    assert not fallback.calls


def test_installation_allowlist_and_free_only(e_fixture):
    reg=registry(e_fixture.store);a=Assignment('jobs','jobs-worker',('one','two'))
    reg.set_installation_policy(InstallationPolicy(('two',)),actor='operator')
    one,two=Fake('synthetic','one'),Fake('synthetic','two')
    assert ModelRouter(reg,a,{'one':one,'two':two},capability='jobs.execute').generate(AIRequest('s','u')).model=='two'
    assert not one.calls
    with pytest.raises(ValueError):InstallationPolicy(('paid',),free_only=False)


def test_mistral_discrepancy_explicit_and_scoped(e_fixture):
    router=legacy_job_router(Fake('qwen','q',ProviderUnavailable()),Fake('mistral','m'),store=e_fixture.store)
    assert router.registry.models['jobs.mistral'].cost==Cost.LEGACY_UNRESOLVED
    assert router.generate(AIRequest('s','u')).provider=='mistral'
    router.registry.set_installation_policy(InstallationPolicy(('jobs.qwen','jobs.mistral'),legacy_job_compatibility=False),actor='explicit-test-policy')
    with pytest.raises(GatewayError):router.generate(AIRequest('s','u'))
    with pytest.raises(ValueError):Assignment('other','other',('jobs.qwen','jobs.mistral'),'JOB_LEGACY_COMPATIBILITY')


@pytest.mark.parametrize('change',['disable','policy','assignment'])
def test_inflight_revalidation(e_fixture,change):
    reg=registry(e_fixture.store);a=Assignment('jobs','jobs-worker',('one','two'))
    def mutate(request):
        if change=='disable':reg.set_global_state('one','DISABLED',actor='operator')
        elif change=='policy':reg.set_installation_policy(InstallationPolicy(('two',)),actor='operator')
        else:reg.assign(replace(a,models=('two',)),capability='jobs.execute')
        return AIResponse('synthetic','one','discard me')
    fallback=Fake('synthetic','two')
    with pytest.raises(GatewayError,match='changed'):ModelRouter(reg,a,{'one':Fake('synthetic','one',mutate),'two':fallback},capability='jobs.execute').generate(AIRequest('s','u'))
    assert not fallback.calls


def test_state_persists_across_registry_restart(e_fixture):
    first=registry(e_fixture.store);first.set_global_state('one','DISABLED',actor='operator')
    assert registry(e_fixture.store).state('one')==ModelState.DISABLED


def test_existing_build_interface_uses_compatibility_profile(monkeypatch):
    import gateway.main as main
    for key,value in {'FREE_ONLY':'TRUE','MISTRAL_PAID_ALLOWED':'FALSE','GROQ_API_KEY':'synthetic','MISTRAL_API_KEY':'synthetic','QWEN_MODEL':'q','MISTRAL_MODEL':'m'}.items():monkeypatch.setenv(key,value)
    monkeypatch.setattr(main,'QwenFreeProvider',lambda key,model:Fake('qwen',model))
    monkeypatch.setattr(main,'MistralFreeProvider',lambda key,model:Fake('mistral',model))
    gateway=main.build_gateway()
    assert gateway.default.models==('jobs.qwen','jobs.mistral')
    assert gateway.generate(AIRequest('s','u')).provider=='qwen'


def test_unmigrated_d_does_not_autocreate_tables(d_base):
    from database.execution_migrations import schema_ready
    router=legacy_job_router(Fake('qwen','q'),Fake('mistral','m'),store=d_base.store)
    assert router.generate(AIRequest('s','u')).provider=='qwen'
    with d_base.store._connect() as con:assert not schema_ready(con)


@pytest.mark.parametrize('response',[None,AIResponse('synthetic','one',''),AIResponse('synthetic','paid','unexpected')])
def test_invalid_model_result_cannot_trigger_fallback(e_fixture,response):
    reg=registry(e_fixture.store);a=Assignment('jobs','jobs-worker',('one','two'));fallback=Fake('synthetic','two')
    primary=Fake('synthetic','one',lambda request:response)
    with pytest.raises(InvalidProviderResponse):ModelRouter(reg,a,{'one':primary,'two':fallback},capability='jobs.execute').generate(AIRequest('s','u'))
    assert not fallback.calls


def test_same_domain_other_agent_assignment_is_independent(e_fixture):
    reg=registry(e_fixture.store);a=Assignment('jobs','jobs-worker',('one',));b=Assignment('jobs','another-agent',('two',))
    reg.assign(a,capability='jobs.execute')
    with worker_context(AgentDefinition('another-agent','jobs',frozenset({'jobs.execute'}))):reg.assign(b,capability='jobs.execute')
    assert reg.assignment(a)==a and reg.assignment(b)==b


def test_capability_pause_stops_model_before_transport(e_fixture):
    reg=registry(e_fixture.store);a=Assignment('jobs','jobs-worker',('one',));transport=Fake('synthetic','one')
    with worker_context(AgentDefinition('jobs-worker','jobs',frozenset({'jobs.execute'})),allowed=lambda domain:False):
        with pytest.raises(PermissionError):ModelRouter(reg,a,{'one':transport},capability='jobs.execute').generate(AIRequest('s','u'))
    assert not transport.calls
