"""Explicit Python dependency profiles; selection never enables capabilities."""
import json
from packaging.requirements import Requirement, InvalidRequirement
from packaging.utils import canonicalize_name
from compatibility.manifest import _unique_object
from installation.dependencies import locked_requirements

PROFILES = ('core', 'scrapy', 'test')


def selected_profiles(components):
    if not isinstance(components, (list, tuple)) or any(x not in PROFILES for x in components):
        raise ValueError('Supported dependency profiles are core, scrapy and test; other runtimes require separate qualification.')
    if len(set(components)) != len(components):
        raise ValueError('Duplicate dependency profile.')
    if 'test' in components:
        if len(components) != 1:
            raise ValueError('The test profile is separate from production component selection.')
        return ['test']
    return ['core'] + (['scrapy'] if 'scrapy' in components else [])


def source_profile(archive, prefix, family, profile):
    if profile not in PROFILES or family not in ('windows', 'ubuntu'):
        raise ValueError('Unsupported dependency profile or operating system.')
    suffix = '' if profile == 'core' else '-extended'
    stem = prefix + 'release/' + family + suffix
    try:
        lock = archive.read(stem + '-hashed.txt').decode('utf-8')
        inventory = json.loads(archive.read(stem + '-inventory.json'), object_pairs_hook=_unique_object)
        requirements = ['requirements.txt'] if profile == 'core' else ['requirements.txt', 'requirements-scrapy.txt'] if profile == 'scrapy' else ['requirements-test.txt']
        pins = locked_requirements(lock)
        visited = set()
        def check(name):
            if name in visited:
                return
            if name not in {'requirements.txt','requirements-scrapy.txt','requirements-test.txt','requirements-installer.txt'}:
                raise ValueError('Unsupported dependency include.')
            visited.add(name)
            text = archive.read(prefix + name).decode('utf-8')
            if len(text) > 65536:
                raise ValueError('Requirements exceed limit.')
            for line in text.splitlines():
                line=line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('-r '):
                    check(line[3:].strip());continue
                requirement=Requirement(line)
                # Existing Chief requirement files have no dynamic platform
                # selectors or URLs. Do not silently omit a future dependency.
                if requirement.url or requirement.marker or requirement.extras:
                    raise ValueError('Dependency selectors require explicit profile review.')
                pin=pins.get(canonicalize_name(requirement.name))
                if pin is None or pin['version'] not in requirement.specifier:
                    raise ValueError('Selected dependency profile is incomplete or incompatible with its requirements.')
        for name in requirements:
            check(name)
    except (KeyError, UnicodeError, InvalidRequirement) as exc:
        raise ValueError('Selected dependency profile inputs are missing or invalid.') from exc
    return lock, inventory
