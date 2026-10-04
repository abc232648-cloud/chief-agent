from dataclasses import replace
from datetime import timedelta
import sqlite3
import pytest
from tests.checkpoint_d_fixture import c_fixture, d_fixture
from evidence.contracts import Evidence, TruthType, Verification
from evidence.service import EvidenceService
from operations.time_integrity import TimeReceipt, utc_now, utc_text
from domains.contracts import AgentDefinition
from security.permissions import worker_context
from domains.jobs.evidence_adapter import JobEvidence


@pytest.mark.parametrize('truth',list(TruthType))
@pytest.mark.parametrize('state',list(Verification))
def test_independent_dimensions(d_fixture,truth,state):
    service=EvidenceService(d_fixture.store,'jobs','jobs.execute')
    identity=service.create(Evidence(truth,'jobs','document','source-1','content-1',verification=state,verification_basis='basis-1'))
    assert service.get(identity)['truth']==truth
    assert service.get(identity)['verification']==state
    assert [x['status'] for x in d_fixture.store.candidate_facts()]==['REVOKED','USER_CONFIRMED','PROPOSED']


def test_append_history_conflicts_and_fresh_assessment(d_fixture):
    s=EvidenceService(d_fixture.store,'jobs','jobs.execute')
    a=s.create(Evidence('DOCUMENTED','jobs','document','one','one'))
    b=s.create(Evidence('OBSERVED','jobs','observation','two','two'))
    s.quality(a,persist=True)
    conflict=s.contradict(a,b,'DIFFERENT_VALUES')
    assert s.quality(a).authority_restriction=='BLOCKED'
    s.verify(a,'PARTIALLY_VERIFIED','review-1');s.verify(a,'VERIFIED','review-2')
    assert s.quality(a).authority_restriction=='BLOCKED'
    s.contradict(a,b,'RESOLVED_BY_REVIEW',conflict_id=conflict,resolved=True)
    s.quality(a,persist=True)
    h=s.history(a)
    assert h['original']['verification']=='UNVERIFIED'
    assert len(h['verification'])==len(h['contradictions'])==len(h['quality'])==2
    with d_fixture.store._connect() as con:
        for table in ('shared_evidence','evidence_verifications','evidence_contradictions','evidence_quality_assessments'):
            with pytest.raises(sqlite3.IntegrityError):con.execute('DELETE FROM '+table)


def test_domain_isolation_on_every_operation(d_fixture):
    s=EvidenceService(d_fixture.store,'jobs','jobs.execute')
    identity=s.create(Evidence('DOCUMENTED','jobs','document','one','one'))
    with worker_context(AgentDefinition('farm-test','farming',frozenset({'farming.records.read'}))):
        other=EvidenceService(d_fixture.store,'farming','farming.records.read')
        own=other.create(Evidence('MEASURED','farm-device','device','sensor','reading'))
        for operation in (lambda:s.get(identity),lambda:JobEvidence(d_fixture.store).confirmed(),lambda:s.history(identity)):
            with pytest.raises(PermissionError):operation()
        for operation in (lambda:other.get(identity),lambda:other.verify(identity,'VERIFIED','review'),lambda:other.quality(identity),lambda:other.contradict(own,identity,'CONFLICT')):
            with pytest.raises(LookupError):operation()


@pytest.mark.parametrize('kwargs',[{'confidence':float('nan')},{'reliability':2},{'source_ref':'https://private.test'},{'verification':'VERIFIED'},{'calibration':'invented'}])
def test_invalid_contracts_rejected(kwargs):
    base=dict(truth='INFERRED',source_owner='jobs',source_kind='document',source_ref='ref',content_ref='content')
    base.update(kwargs)
    with pytest.raises(ValueError):Evidence(**base)
