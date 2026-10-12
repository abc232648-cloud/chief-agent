import ast
from pathlib import Path

from application.composition import default_catalogs, default_registry
from runtime_qualification.catalog import load_candidates
from runtime_qualification.evaluator import eligible_candidates
from runtime_qualification.policy import load_policy

ROOT=Path(__file__).resolve().parents[1]


def test_agent_registry_domain_payload_is_unchanged_by_g():
    registry=default_registry()
    assert set(registry.domains)=={'jobs','farming'}
    assert set(registry.agents)=={'jobs-worker','farming-recorder'}


def test_g_capabilities_are_separate_non_authorizing_surfaces():
    caps=default_catalogs().capabilities
    update=caps.capabilities['chief.update_planning']
    runtime=caps.capabilities['chief.runtime_qualification']
    assert update.permissions==runtime.permissions==()
    assert update.side_effects==runtime.side_effects==()
    assert update.mode.value=='ENABLED'
    assert runtime.mode.value=='SHADOW'


def test_runtime_catalog_is_research_snapshot_not_selection():
    candidates=load_candidates((ROOT/'config/runtime_candidates.json').read_text(encoding='utf-8'))
    policy=load_policy((ROOT/'config/runtime_policy.json').read_text(encoding='utf-8'),candidates)
    roles={policy.execution_candidate_id:policy.execution_role,policy.research_candidate_id:policy.research_role}
    assert set(roles)=={candidate.id for candidate in candidates}
    eligible,results=eligible_candidates(candidates,{},roles)
    assert eligible==()
    assert all(not r.qualified for r in results)


def test_no_runtime_vendor_package_or_install_command_is_imported_by_chief_code():
    forbidden={'openclaw','hermes','hermes_agent'}
    for package in ('compatibility','update_center','runtime_qualification'):
        for path in (ROOT/package).rglob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8'))
            imports=set()
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):imports.update(x.name.split('.')[0] for x in node.names)
                elif isinstance(node,ast.ImportFrom) and node.module:imports.add(node.module.split('.')[0])
            assert not imports & forbidden, (path,imports & forbidden)


def test_update_discovery_has_metadata_transport_but_no_install_or_activation_authority():
    update_files=tuple((ROOT/'update_center').rglob('*.py'))
    compatibility_files=tuple((ROOT/'compatibility').rglob('*.py'))
    text='\n'.join(path.read_text(encoding='utf-8') for path in compatibility_files+update_files)
    forbidden=('subprocess','requests.','httpx','pip install','npm install','openclaw update','hermes update','shutil.copytree(','os.system(','Popen(','check_call(','check_output(')
    assert all(token not in text for token in forbidden)

    network_importers=[]
    for path in update_files:
        tree=ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom) and node.module=='urllib.request':
                if any(alias.name=='urlopen' for alias in node.names):network_importers.append(path.relative_to(ROOT).as_posix())
            elif isinstance(node,ast.Import):
                if any(alias.name=='urllib.request' for alias in node.names):network_importers.append(path.relative_to(ROOT).as_posix())
    assert network_importers==['update_center/dependency_check.py']
    discovery=(ROOT/'update_center/dependency_check.py').read_text(encoding='utf-8')
    assert '_ALLOWED_HOSTS = {"api.github.com", "pypi.org"}' in discovery
    assert 'parsed.scheme != "https"' in discovery
    assert 'os.replace(temp_name, path)' in discovery


def test_docker_payload_contains_g_foundation_packages():
    docker=(ROOT/'gateway/Dockerfile').read_text(encoding='utf-8')
    for line in ('COPY compatibility ./compatibility','COPY update_center ./update_center','COPY runtime_qualification ./runtime_qualification'):
        assert line in docker
