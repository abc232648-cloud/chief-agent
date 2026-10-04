from pathlib import Path
import pytest
from tests.checkpoint_e_fixture import d_base,e_fixture
from application.composition import default_catalogs
from application.control_services import compose_control_services
from capabilities.contracts import Node,Mode


def test_models_and_sop_do_not_add_grants_or_runtime_modes(e_fixture):
    catalog=default_catalogs()
    assert catalog.agents.resolve('jobs')[1].capabilities==frozenset({'jobs.execute'})
    controls=compose_control_services(e_fixture.store,catalog).controls
    for name in ('chief.model_registry','chief.runbooks'):
        with pytest.raises(ValueError):controls.preview(Node('component',name),Mode.DISABLED)
    assert not any('openclaw' in key.lower() or 'hermes' in key.lower() for key in catalog.capabilities.components)


def test_job_pipeline_does_not_depend_on_runbook_engine():
    root=Path(__file__).resolve().parents[1]
    for name in ('worker/command_processor.py','worker/application_executor.py','worker/final_submission.py','domains/jobs/ledger_adapter.py'):
        source=(root/name).read_text()
        assert 'runbooks' not in source
    docker=(root/'gateway/Dockerfile').read_text()
    for name in ('model_registry','runbooks'):assert 'COPY '+name+' ./'+name in docker
