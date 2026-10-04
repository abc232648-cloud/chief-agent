from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from browser.playwright_reader import BrowserReadConfig, BrowserReadError, PlaywrightReader


def test_url_validation_rejects_non_http():
    reader = PlaywrightReader()
    with pytest.raises(BrowserReadError):
        reader.read_url("file:///etc/passwd")


def test_domain_allowlist_blocks_unapproved_host():
    reader = PlaywrightReader(BrowserReadConfig(allowed_domains=("example.com",)))
    with pytest.raises(BrowserReadError, match="allowlist"):
        reader.read_url("https://evil.example.net/job")


def test_playwright_missing_is_clean_error(monkeypatch):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "playwright.sync_api":
            raise ImportError("missing playwright")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    with pytest.raises(BrowserReadError, match="not installed"):
        PlaywrightReader().read_url("https://example.com")


def test_reader_extracts_only_page_data(monkeypatch):
    class FakeLocator:
        def __init__(self, value):
            self.value = value
        def inner_text(self, **kwargs):
            return self.value
        def get_attribute(self, name):
            return "https://example.com/canonical" if name == "href" else None
        def count(self): return 1
        @property
        def first(self): return self
        def evaluate_all(self, expression): return [{'text':'Job','url':'https://example.com/jobs/1'}]

    class FakePage:
        def goto(self, *args, **kwargs):
            return None
        def title(self):
            return "Security Analyst"
        def locator(self, selector):
            if selector == "body":
                return FakeLocator("SOC Analyst\nRemote\nSalary")
            return FakeLocator("")

    class FakeBrowser:
        def new_context(self,**kwargs):
            return SimpleNamespace(new_page=self.new_page,route=lambda *a:None,route_web_socket=lambda *a:None,add_init_script=lambda **kw:None)
        def new_page(self):
            return FakePage()
        def close(self):
            return None

    class FakeChromium:
        def launch(self, **kwargs):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    fake_module = ModuleType("playwright.sync_api")
    fake_module.sync_playwright = lambda: FakePlaywright()
    fake_pkg = ModuleType("playwright")
    fake_pkg.sync_api = fake_module
    monkeypatch.setitem(sys.modules, "playwright", fake_pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)

    result = PlaywrightReader(BrowserReadConfig(allowed_domains=("example.com",))).read_url(
        "https://example.com/jobs/1"
    )
    assert result.source == "playwright"
    assert result.url == "https://example.com/canonical"
    assert result.payload["title"] == "Security Analyst"
    assert "SOC Analyst" in result.payload["text"]
    assert result.payload["source_url"] == "https://example.com/jobs/1"
