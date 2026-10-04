from dataclasses import asdict,replace
from datetime import timedelta
import pytest
from data_quality.engine import assess
from evidence.contracts import Evidence
from operations.time_integrity import TimeReceipt,ClockQuality,utc_now,utc_text


@pytest.mark.parametrize('truth',['MEASURED','OBSERVED','DOCUMENTED','INFERRED'])
@pytest.mark.parametrize('existing',['ALLOW','ASK','BLOCK'])
def test_unknowns_never_invent_or_escalate(truth,existing):
    quality=assess(asdict(Evidence(truth,'owner','source','ref','content')))
    assert quality.confidence is None and quality.sufficiency=='INCOMPLETE'
    assert quality.restrict(existing)==existing


@pytest.mark.parametrize('problem',['stale','future','receipt_future','disputed','conflict','calibration','reliability','clock'])
def test_adverse_quality_restricts_without_rewriting(problem):
    now=utc_now()
    record=asdict(Evidence('MEASURED','owner','device','ref','content',time=TimeReceipt(utc_text(now),utc_text(now)),confidence=.8))
    if problem=='stale':record['time']['source_at']=utc_text(now-timedelta(days=500))
    if problem=='future':record['time']['source_at']=utc_text(now+timedelta(days=1))
    if problem=='receipt_future':record['time']['received_at']=utc_text(now+timedelta(days=1))
    if problem=='disputed':record['verification']='DISPUTED'
    if problem=='calibration':record['calibration']='INVALID'
    if problem=='reliability':record['reliability']=.2
    if problem=='clock':record['time']['clock']['quality']='UNRELIABLE'
    quality=assess(record,now=now,contradictions=problem=='conflict')
    assert quality.confidence<=.25 and quality.sufficiency=='INSUFFICIENT'
    assert all(quality.restrict(x)=='BLOCK' for x in ('ALLOW','ASK','BLOCK'))
    assert record['confidence']==.8


def test_complete_evidence_still_cannot_authorize():
    now=utc_text(utc_now())
    record=asdict(Evidence('MEASURED','owner','device','ref','content',time=TimeReceipt(now,now,ClockQuality(quality='ASSERTED')),verification='VERIFIED',verification_basis='review',confidence=.8,reliability=.9,calibration='VALID'))
    q=assess(record)
    assert q.sufficiency=='SUFFICIENT' and q.confidence==.8
    assert q.restrict('ASK')=='ASK' and q.restrict('BLOCK')=='BLOCK'


def test_future_at_receipt_does_not_silently_become_reliable():
    now=utc_now()
    record=asdict(Evidence('OBSERVED','owner','source','ref','content',time=TimeReceipt(utc_text(now-timedelta(days=2)),utc_text(now-timedelta(days=1)))))
    q=assess(record,now=now)
    assert q.dimensions['source_at_receipt']=='FUTURE' and q.authority_restriction=='BLOCKED'
