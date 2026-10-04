from dataclasses import replace
from types import MappingProxyType
from gateway.errors import GatewayError,ProviderUnavailable,InvalidProviderResponse
from gateway.models import AIRequest,AIResponse
from security.permissions import current_context
from .contracts import Assignment,Provider,Model,Cost
from .registry import ModelRegistry


class ModelRouter:
    def __init__(self,registry,assignment,transports,*,capability,allow_unscoped=False):
        self.registry,self.default=registry,assignment
        self.transports=MappingProxyType(dict(transports))
        self.capability,self.allow_unscoped=capability,allow_unscoped

    def generate(self,request):
        if not isinstance(request,AIRequest):raise TypeError('AIRequest is required.')
        context=current_context()
        if context is None:
            if not self.allow_unscoped:raise PermissionError('Active model consumer context required.')
        else:
            if (context.domain,context.agent_id)!=(self.default.domain,self.default.agent):
                raise PermissionError('Model assignment belongs to another domain/agent.')
            context.require(self.capability)
        assignment=self.registry.assignment(self.default)
        for index,identity in enumerate(assignment.models):
            if not self.registry.eligible(identity,assignment):continue
            transport=self.transports.get(identity)
            if transport is None:raise GatewayError('STOP: Configured model transport is unavailable')
            try:
                response=transport.generate(request)
            except ProviderUnavailable:
                continue
            # A disable, policy change or reassignment during inference invalidates the response.
            if self.registry.assignment(self.default)!=assignment or not self.registry.eligible(identity,assignment):
                raise GatewayError('STOP: Model configuration changed during inference')
            if context is not None:context.require(self.capability)
            if not isinstance(response,AIResponse) or not isinstance(response.text,str) or not response.text:
                raise InvalidProviderResponse('Model returned invalid content')
            if response.model!=self.registry.models[identity].provider_model:
                raise InvalidProviderResponse('Response model differs from the configured route')
            return replace(response,fallback_used=index>0 or response.fallback_used)
        raise GatewayError('STOP: No approved free AI route is available')


def legacy_job_router(qwen,mistral,*,store=None):
    models=(Model('jobs.qwen','groq',qwen.model,Cost.FREE),Model('jobs.mistral','mistral',mistral.model,Cost.LEGACY_UNRESOLVED))
    registry=ModelRegistry((Provider('groq'),Provider('mistral')),models,store=store,legacy_pair=('jobs.qwen','jobs.mistral'))
    assignment=Assignment('jobs','jobs-worker',('jobs.qwen','jobs.mistral'),'JOB_LEGACY_COMPATIBILITY')
    return ModelRouter(registry,assignment,{'jobs.qwen':qwen,'jobs.mistral':mistral},capability='jobs.execute',allow_unscoped=True)
