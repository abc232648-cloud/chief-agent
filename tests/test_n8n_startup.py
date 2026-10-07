import pytest
from integrations import n8n_startup as startup


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.setattr(startup.n8n_handoff, '_configuration', lambda: ('endpoint','private-token'))
    monkeypatch.setattr(startup.time, 'sleep', lambda _: None)
    calls=[]
    def configure(outcomes):
        values=iter(outcomes)
        def exchange(parsed, token, body):
            calls.append(body)
            return next(values)
        monkeypatch.setattr(startup.n8n_handoff, '_exchange', exchange)
        return calls
    return configure


@pytest.mark.parametrize('outcomes,expected,count',[
    ([True,True],'STARTUP_READY',2),
    ([False,True,True],'STARTUP_READY',3),
    ([False,False,False],'NOT_READY',3),
    ([True,False,True],'NOT_READY',3),
    ([False,False,True],'NOT_READY',3),
])
def test_gate_requires_consecutive_receipts_and_is_bounded(probe,outcomes,expected,count):
    calls=probe(outcomes)
    result=startup.qualify(confirmed=True)
    assert result['status']==expected
    assert len(calls)==count
    assert len({b['operation_id'] for b in calls})==count
    assert all(b['purpose']=='synthetic-handshake-only' and b['domain']=='chief' for b in calls)
    assert result['business_execution_authorized'] is False
    assert 'private-token' not in str(result)


def test_explicit_confirmation_required(probe):
    calls=probe([True,True])
    with pytest.raises(ValueError):startup.qualify()
    assert calls==[]


def test_slow_receipts_do_not_qualify(monkeypatch,probe):
    probe([True,True,True])
    ticks=iter([0,0,6,6,12,12,18,18])
    monkeypatch.setattr(startup.time,'monotonic',lambda:next(ticks))
    assert startup.qualify(confirmed=True)['status']=='NOT_READY'


def test_expired_receipts_do_not_qualify(monkeypatch,probe):
    probe([True,True,True])
    ticks=iter([0,16,16,32,32,48])
    monkeypatch.setattr(startup.time,'time',lambda:next(ticks))
    assert startup.qualify(confirmed=True)['status']=='NOT_READY'
