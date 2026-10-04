from html.parser import HTMLParser
from pathlib import Path
import json,subprocess,shutil
import pytest
from tests.test_identity_http import request
ROOT=Path(__file__).resolve().parents[1]

class Assets(HTMLParser):
    def __init__(self):super().__init__();self.tags=[]
    def handle_starttag(self,tag,attrs):self.tags.append((tag,dict(attrs)))

def test_html_separation_accessibility_and_foundation_surfaces():
    parser=Assets();parser.feed((ROOT/'dashboard.html').read_text(encoding='utf-8'))
    assert not any(t=='style' or 'style' in a or any(k.lower().startswith('on') for k in a) or (t=='script' and not a.get('src')) for t,a in parser.tags)
    ids=[a['id'] for _,a in parser.tags if 'id' in a];assert len(ids)==len(set(ids))
    assert {'health','components','models','runtime','evidence','ledger','runbooks','mainContent'}<=set(ids)
    assert ('html',{'lang':'en'}) in parser.tags
    for t,a in parser.tags:
        if t=='script':assert (ROOT/a['src'].lstrip('/')).is_file()

def test_pwa_remains_private_network_required():
    manifest=json.loads((ROOT/'static/app.webmanifest').read_text())
    assert manifest['start_url']=='/' and manifest['scope']=='/' and manifest['display']=='standalone'
    assert all((ROOT/i['src'].lstrip('/')).is_file() for i in manifest['icons'])
    assert 'Chief pages require a network connection' in (ROOT/'dashboard.html').read_text(encoding='utf-8')
    registrations=[p.name for p in (ROOT/'static').glob('*.js') if 'serviceWorker.register' in p.read_text(encoding='utf-8')]
    assert registrations==['work.js']
    work=(ROOT/'static/work.js').read_text(encoding='utf-8')
    assert "serviceWorker.register('/farm-sw.js',{scope:'/work'})" in work
    sw=(ROOT/'static/farm-sw.js').read_text(encoding='utf-8')
    assert "request.method!=='GET'||url.origin!==self.location.origin" in sw
    assert "url.pathname==='/work'" in sw
    assert 'ASSETS.includes(url.pathname)' in sw

@pytest.mark.parametrize('asset',['chief-navigation.js','chief-foundation.js'])
def test_new_assets_get_head_and_security(dashboard,asset):
    path='/static/'+asset
    code,headers,data=request(dashboard,path)
    assert code==200 and data==(ROOT/path.lstrip('/')).read_bytes()
    assert headers['Cache-Control']=='no-store' and headers['X-Content-Type-Options']=='nosniff'
    assert request(dashboard,path,'HEAD')[0]==200
    _,headers,_=request(dashboard,'/',raw=dashboard.credentials['raw'])
    assert "script-src 'self'" in headers['Content-Security-Policy']

def test_navigation_role_visibility_and_safe_rendering_in_node():
    node=shutil.which('node')
    assert node,'Node is required for the executable non-browser UI contract test.'
    result=subprocess.run([node,str(ROOT/'tests/h_navigation_checks.js')],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
