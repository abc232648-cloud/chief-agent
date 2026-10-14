"""Read-only Chief release preflight. A declaration is not executed acceptance."""
from dataclasses import asdict, dataclass
import platform
import sys

from update_center.planner import plan_update
from update_center.release_trust import verify_release, python_version


@dataclass(frozen=True)
class HostEnvironment:
    os: str
    architecture: str
    python: str
    implementation: str = 'cpython'
    final_python: bool = True


def observe_host():
    architecture = {'amd64': 'x86_64', 'x86_64': 'x86_64', 'arm64': 'aarch64', 'aarch64': 'aarch64'}.get(platform.machine().lower(), 'unknown')
    return HostEnvironment({'win32': 'windows', 'linux': 'linux'}.get(sys.platform, 'unknown'),
                           architecture, '.'.join(str(v) for v in sys.version_info[:3]),
                           sys.implementation.name, sys.version_info.releaselevel == 'final')


def assess(envelope, keys, current, registry, *, minimum_sequence, environment=None, now=None):
    """Verify again, then compute requirements using the current Chief catalog.

    No test results, backup success or approval are inferred from release metadata.
    Schema changes stay blocked until a separate migration path is qualified.
    """
    release = verify_release(envelope, keys, minimum_sequence=minimum_sequence, now=now)
    host = environment if environment is not None else observe_host()
    if not isinstance(host, HostEnvironment):
        raise ValueError('Explicit host observation required.')
    blockers = []
    eligible = [t for t in release.targets if t.os == host.os and t.architecture == host.architecture]
    if not eligible:
        blockers.append('HOST_PLATFORM_NOT_SUPPORTED')
    try:
        actual = python_version(host.python)
        python_ok = eligible and python_version(eligible[0].python_minimum) <= actual < python_version(eligible[0].python_before)
    except ValueError:
        python_ok = False
    if not python_ok or host.implementation != 'cpython' or host.final_python is not True:
        blockers.append('HOST_PYTHON_NOT_SUPPORTED')
    plan = plan_update(current, release.manifest, registry)
    blockers.extend(plan.blockers)
    if current.schema_sha256 != release.manifest.schema_sha256 or release.manifest.migrations:
        blockers.append('OPERATIONAL_MIGRATION_NOT_AUTHORIZED')
    return {'status': 'BLOCKED' if blockers else 'ACCEPTANCE_REQUIRED', 'product': 'chief',
            'plan': asdict(plan), 'environment': asdict(host), 'blockers': sorted(set(blockers)),
            'source_sha256': release.manifest.source_tree_sha256, 'envelope_sha256': release.envelope_sha256,
            'artifact_verified': False, 'tests_passed': False, 'backup_verified': False,
            'activation_authorized': False, 'installed': False}
