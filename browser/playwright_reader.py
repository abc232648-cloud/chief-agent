from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse

from .sources.base import SourceListing


class BrowserReadError(RuntimeError):
    """A read-only browser operation failed."""


@dataclass(frozen=True)
class BrowserReadConfig:
    timeout_ms: int = 20_000
    max_text_chars: int = 50_000
    allowed_domains: tuple[str, ...] = ()
    resource_origins: tuple[str, ...] = ()


class PlaywrightReader:
    """Strictly read-only Playwright page reader.

    This class intentionally exposes no click, type, submit, login, download,
    or arbitrary-navigation methods. Page content is untrusted data.
    """

    name = "playwright"

    def __init__(self, config: BrowserReadConfig | None = None, *, access=None, domain=None):
        self.config = config or BrowserReadConfig()
        self.access = access
        self.domain = domain

    def _check_url(self, url: str) -> None:
        from .request_policy import origin, BrowserPolicyBlocked
        try:origin(url)
        except BrowserPolicyBlocked as exc:raise BrowserReadError(str(exc)) from None
        parsed = urlparse(url)
        if (parsed.hostname or '').lower()=='upwork.com' or (parsed.hostname or '').lower().endswith('.upwork.com'):
            raise BrowserReadError('Upwork requires an approved API integration. Browser-session automation is not enabled for this platform.')
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise BrowserReadError("Only absolute HTTP(S) URLs are allowed")
        if self.config.allowed_domains:
            host = parsed.hostname or ""
            allowed = any(host == d or host.endswith("." + d) for d in self.config.allowed_domains)
            if not allowed:
                raise BrowserReadError("URL domain is not on the approved read-only allowlist")

    def read_url(self, url: str) -> SourceListing:
        from security.permissions import require_if_scoped
        require_if_scoped('jobs.execute')
        self._check_url(url)
        record = self.access.assert_active(self.domain) if self.access else None
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserReadError("Playwright is not installed") from exc

        try:
            with sync_playwright() as p:
                from .request_policy import controlled_browser,origin
                with controlled_browser(p,url,storage_state=self.access.state(self.domain) if self.access else None,
                        resource_origins=self.config.resource_origins,
                        revision_check=(lambda:self.access.assert_active(self.domain,record['revision'])) if self.access else None,
                        store=self.access.store if self.access else None) as (context,policy):
                    page = context.new_page()
                    response=page.goto(url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                    if response is not None and response.status >= 400:
                        raise BrowserReadError(f'Source returned HTTP {response.status}; no access-control bypass attempted.')
                    final_url=getattr(page,'url',url)
                    self._check_url(final_url)
                    if origin(final_url)!=origin(url):raise BrowserReadError('Read navigation left its approved origin.')
                    if self.access:
                        password = page.locator('input[type="password"]')
                        if password.count() and password.first.is_visible():
                            with self.access.store._connect() as con:
                                con.execute("UPDATE site_access SET status='LOGIN_REQUIRED',message='Your saved session needs a fresh sign-in.' WHERE domain=? AND status='ACTIVE' AND revision=?", (self.domain, record['revision']))
                            raise BrowserReadError('Sign-in required. Open the website sign-in window from Sources.')
                    title = page.title()
                    text = page.locator("body").inner_text(timeout=self.config.timeout_ms)
                    canonical_node=page.locator('link[rel="canonical"]')
                    canonical=(canonical_node.first.get_attribute('href') if canonical_node.count() else None) or final_url
                    self._check_url(canonical)
                    links=page.locator('a[href]').evaluate_all("els => els.slice(0, 200).map(a => ({text:a.innerText.slice(0,200),url:a.href}))")
                    safe_links=[]
                    for link in links:
                        try:self._check_url(link['url'])
                        except BrowserReadError:continue
                        if urlparse(link['url']).scheme=='https':safe_links.append(link)
                    payload: dict[str, Any] = {
                        "title": title,
                        "text": text[: self.config.max_text_chars],
                        "url": canonical,
                        "source_url": url,
                        "links": safe_links,
                    }
                    if self.access:
                        self.access.assert_active(self.domain, record['revision'])
                    return SourceListing(self.name, canonical, payload)
        except BrowserReadError:
            raise
        except Exception as exc:
            raise BrowserReadError('Read-only page fetch failed or was blocked; no browser details were exposed.') from None


class PlaywrightJobSource:
    """Read a fixed set of approved job URLs using Playwright."""

    name = "playwright"

    def __init__(self, urls: Iterable[str], config: BrowserReadConfig | None = None):
        self.urls = tuple(urls)
        self.reader = PlaywrightReader(config)

    def fetch(self) -> list[SourceListing]:
        return [self.reader.read_url(url) for url in self.urls]
