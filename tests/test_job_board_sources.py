import pytest
from domains.jobs.board_sources import normalize,compare


def row(**kw):return {'id':123,'title':'Junior Engineer','absolute_url':'https://boards.greenhouse.io/example/jobs/123?utm_source=x','location':{'name':'Lagos'},'content':'<p>Build tools</p><script>steal()</script>',**kw}


def test_greenhouse_change_identity_and_partial_absence():
    a=normalize('greenhouse','example',{'jobs':[row()]},fetched_at='2026-01-01T00:00:00Z')
    b=normalize('greenhouse','example',{'jobs':[row(title='Engineer II')]},fetched_at='2026-01-02T00:00:00Z')
    assert a[0]['source_key']==b[0]['source_key'] and a[0]['dedupe_key']==b[0]['dedupe_key']
    assert 'steal' not in a[0]['description'] and a[0]['authority']=='NONE'
    assert compare(a,b)[0]['change']=='CHANGED'
    assert compare(a,[])[0]['change']=='NOT_OBSERVED_IN_PARTIAL_RESPONSE'
    assert compare(a,[],complete=True)[0]['change']=='NOT_LISTED'
    with pytest.raises(ValueError,match='Older'):compare(b,a)


def test_lever_sections_and_unknown_location():
    result=normalize('lever','example',[{'id':'abc','text':'Engineer','hostedUrl':'https://jobs.lever.co/example/abc','descriptionPlain':'Role','lists':[{'text':'Requirements','content':'<li>Python</li>'}]}],fetched_at='2026-01-01T00:00:00Z')
    assert 'Python' in result[0]['description'] and result[0]['location'] is None
    with pytest.raises(ValueError):normalize('greenhouse','example',{'jobs':[row(absolute_url='javascript:alert(1)')]},fetched_at='2026-01-01T00:00:00Z')
    with pytest.raises(ValueError):normalize('greenhouse','example',{'jobs':[row(),row()]},fetched_at='2026-01-01T00:00:00Z')
    with pytest.raises(ValueError):compare(result,normalize('greenhouse','other',{'jobs':[row()]},fetched_at='2026-01-01T00:00:00Z'))
