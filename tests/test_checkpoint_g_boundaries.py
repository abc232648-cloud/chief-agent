import ast
from pathlib import Path

from application.composition import default_catalogs, default_registry
from runtime_qualification.catalog import load_candidates
from runtime_qualification.evaluator import eligible_candidates

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
    candidates=load_candidates((ROOT/'config/runtime_candidates.json').read_text())
    eligible,results=eligible_candidates(candidates,{})
    assert eligible==()
    assert all(not r.qualified for r in results)


def test_no_runtime_vendor_package_or_install_command_is_imported_by_chief_code():
    forbidden={'openclaw','hermes','hermes_agent'}
    for package in ('compatibility','update_center','runtime_qualification'):
        for path in (ROOT/package).rglob('*.py'):
            tree=ast.parse(path.read_text())
            imports=set()
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):imports.update(x.name.split('.')[0] for x in node.names)
                elif isinstance(node,ast.ImportFrom) and node.module:imports.add(node.module.split('.')[0])
            assert not imports & forbidden, (path,imports & forbidden)


def test_no_update_activation_or_download_primitive_exists():
    text='\n'.join(p.read_text() for pkg in ('compatibility','update_center') for p in (ROOT/pkg).rglob('*.py'))
    forbidden=('subprocess','urlopen','requests.','httpx','pip install','openclaw update','hermes update','os.replace(','shutil.copytree(')
    assert all(x not in text for x in forbidden)


def test_docker_payload_contains_g_foundation_packages():
    docker=(ROOT/'gateway/Dockerfile').read_text()
    for line in ('COPY compatibility ./compatibility','COPY update_center ./update_center','COPY runtime_qualification ./runtime_qualification'):
        assert line in docker
