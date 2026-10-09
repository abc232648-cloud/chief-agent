"""Request-time exposure derived from Chief authorities; never an execution grant."""
from agents.interfaces import InterfaceRegistry, InterfaceRegistration
from agents.manifest import interface, validate_against_runtime
from capabilities.contracts import Mode, Node
from control.module_lifecycle import ModuleLifecycle, ModuleState


class ModuleExposure:
    def __init__(self, lifecycle: ModuleLifecycle, interfaces: InterfaceRegistry):
        if not isinstance(lifecycle, ModuleLifecycle) or lifecycle.compatibility is None:
            raise ValueError('Exposure requires A7 lifecycle with A8 compatibility.')
        if not isinstance(interfaces, InterfaceRegistry) or interfaces.manifests is not lifecycle.manifests:
            raise ValueError('Exposure requires the same authoritative interface catalog.')
        self.lifecycle = lifecycle
        self.interfaces = interfaces
        # A replacement, even at the same version, requires recomposition. Never
        # serve cached registrations against a changed installation.
        self._installed = {key: lifecycle.manifests.get(key) for key in lifecycle.manifests.ids()}
        self._runtime = {key: lifecycle.manifests.runtime.resolve(key) for key in self._installed}

    def require(self, module_id, service, principal, *, kind=None):
        try:
            manifests = self.lifecycle.manifests
            manifest = manifests.get(module_id)
            if self._installed.get(module_id) is not manifest:
                raise ValueError('Installed metadata changed.')
            domain, agent = manifests.runtime.resolve(module_id)
            original_domain, original_agent = self._runtime[module_id]
            if domain is not original_domain or agent is not original_agent:
                raise ValueError('Runtime installation changed.')
            if agent.id != manifests.runtime_agent_id(module_id):
                raise ValueError('Runtime registration changed.')
            validate_against_runtime(manifest, domain, agent)
            manifests.capabilities.require_domain_capabilities(module_id, manifest.capabilities)
            snapshot = self.lifecycle.snapshot(module_id)
            if snapshot.compatible is not True or snapshot.state not in {
                ModuleState.ENABLED, ModuleState.DEGRADED, ModuleState.UPDATE_AVAILABLE,
            }:
                raise ValueError('Module is not enabled and compatible.')
            controls = self.lifecycle.controls
            if controls.desired(Node('component', module_id)).mode != Mode.ENABLED:
                raise ValueError('Domain is disabled.')
            if not controls.allowed(agent.id):
                raise ValueError('Component or dependency is disabled.')
            principal = service.refresh(principal)
            service.authorize(principal, 'work.read', module_id, sensitive=False)
            registration = None
            if kind is not None:
                registration = self.interfaces.require(module_id, kind)
                declaration = interface(manifest, kind)
                if declaration is None or registration != InterfaceRegistration.from_manifest(manifest, declaration):
                    raise ValueError('Interface registration is stale.')
                if principal.role not in registration.roles:
                    raise PermissionError('Interface is not available to this role.')
            return registration
        except (ValueError, KeyError, AttributeError) as exc:
            raise PermissionError('Module surface is unavailable; refresh installed state.') from exc

    def available(self, module_id, service, principal, *, kind=None):
        try:
            self.require(module_id, service, principal, kind=kind)
            return True
        except PermissionError:
            return False

    def describe(self, service, principal):
        result = []
        for kind in ('owner', 'staff', 'companion'):
            for module_id in self._installed:
                try:
                    registration = self.require(module_id, service, principal, kind=kind)
                except PermissionError:
                    continue
                result.append(registration.as_dict())
        return result
