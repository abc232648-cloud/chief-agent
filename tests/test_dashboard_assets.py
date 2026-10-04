from pathlib import Path
from html.parser import HTMLParser
import re

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'dashboard.html').read_text()
JS = (ROOT / 'static' / 'dashboard.js').read_text()
APP = (ROOT / 'dashboard_app.py').read_text()

class Parser(HTMLParser):
    pass

Parser().feed(HTML)
assert '<script>' not in HTML
assert '<style>' not in HTML
assert not re.search(r'\bon(?:click|change|input|submit)=', HTML, re.I)
assert not re.search(r'\bstyle=', HTML, re.I)
assert 'href="/static/dashboard.css"' in HTML
assert '<script src="/static/dashboard.js" defer></script>' in HTML
assert 'path.startswith(\'/static/\')' in APP
assert "self.serve_static(DASHBOARD_HTML, 'text/html; charset=utf-8', csp=True)" in APP
assert "Content-Security-Policy" in APP
assert 'onclick=' not in JS
print('dashboard asset architecture: PASS')
