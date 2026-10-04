from pathlib import Path
import pytest
from tests.checkpoint_d_fixture import c_fixture,d_fixture
from application.composition import default_catalogs
from application.control_services import compose_control_services
from capabilities.contracts import Node,Mode
from security.permissions import worker_context
from domains.contracts import AgentDefinition
from domains.jobs.ledger_adapter import JobSubmissionExecutor
from domains.jobs.evidence_adapter import JobEvidence
from evidence.service import EvidenceService
from worker.action_gate import ApprovalRequired


def test_d_catalog_does_not_grant_or_claim_runtime_support(d_fixture):
    catalogs=default_catalogs()
    assert catalogs.agents.resolve('jobs')[1].capabilities==frozenset({'jobs.execute'})
    for name in ('evidence','data_quality','decision_ledger'):
        service='chief.'+name
        controls=compose_control_services(d_fixture.store,catalogs).controls
        with pytest.raises(ValueError):controls.preview(Node('component',service),Mode.DISABLED)
    docker=(Path(__file__).resolve().parents[1]/'gateway/Dockerfile').read_text()
    for name in ('evidence','data_quality','decision_ledger'):assert 'COPY '+name+' ./'+name in docker


def test_dashboard_retry_adapter_keeps_approval_and_live_facts(d_fixture):
    executor=JobSubmissionExecutor(d_fixture.store)
    assert executor.candidate_fact_provider.__self__.__class__ is JobEvidence
    with pytest.raises(ApprovalRequired):executor.submit('app-fixture','https://synthetic.test','button',approved=False)
    a=JobEvidence(d_fixture.store);e=a.adapt(d_fixture.fact_ids[1],truth='DOCUMENTED')
    assert executor.candidate_fact_provider()
    a.shared.verify(e,'DISPUTED','review')
    assert not executor.candidate_fact_provider()


def test_shared_access_honors_legacy_pause_and_capability(d_fixture):
    service=EvidenceService(d_fixture.store,'jobs','jobs.execute')
    for capabilities,allowed in [(frozenset(),lambda d:True),(frozenset({'jobs.execute'}),lambda d:False)]:
        with worker_context(AgentDefinition('test','jobs',capabilities),allowed):
            with pytest.raises(PermissionError):service.ready()


def test_no_core_domain_imports_added():
    from tests.test_architecture_boundaries import test_chief_core_cannot_reach_domain_implementations_or_composition
    test_chief_core_cannot_reach_domain_implementations_or_composition()
