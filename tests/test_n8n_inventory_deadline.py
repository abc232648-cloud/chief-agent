import pytest
from integrations import n8n


def connection(monkeypatch, *, chunks, times):
    clock=iter(times);reads=[];timeouts=[];closed=[]
    monkeypatch.setattr(n8n.time,'monotonic',lambda:next(clock))
    monkeypatch.setenv('CHIEF_N8N_URL','http://127.0.0.1:5678')
    monkeypatch.setenv('CHIEF_N8N_API_KEY','synthetic-token')
    data=iter(chunks)
    class Socket:
        def settimeout(self,value):timeouts.append(value)
    class Response:
        status=200
        def read1(self,limit):reads.append(limit);return next(data)
    class Connection:
        sock=Socket()
        def __init__(self,*args,**kwargs):pass
        def request(self,*args,**kwargs):pass
        def getresponse(self):return Response()
        def close(self):closed.append(True)
    monkeypatch.setattr(n8n.http.client,'HTTPConnection',Connection)
    return reads,timeouts,closed


def test_trickling_inventory_has_total_deadline_without_retry(monkeypatch):
    reads,timeouts,closed=connection(monkeypatch,chunks=[b'{',b'"'],times=[0,1,4,6])
    with pytest.raises(ValueError,match='deadline'):n8n.inventory()
    assert len(reads)==2 and timeouts==[4,1] and closed==[True]


def test_complete_response_over_deadline_is_not_published(monkeypatch):
    reads,_,closed=connection(monkeypatch,chunks=[b'{"data":[]}',b''],times=[0,1,2,6])
    with pytest.raises(ValueError,match='deadline'):n8n.inventory()
    assert len(reads)==2 and closed==[True]


def test_size_boundary_still_rejects_oversized_inventory(monkeypatch):
    reads,_,closed=connection(monkeypatch,chunks=[b'x'*1_000_001],times=[0,1,2])
    with pytest.raises(ValueError,match='limit'):n8n.inventory()
    assert reads==[1_000_001] and closed==[True]
