# Browser / Job Sources

This layer is deliberately **read-only at the ingestion stage**.

## Current adapters

- `browser.sources.JsonJobSource`: deterministic local/exported job-listing input.
- `browser.PlaywrightReader`: fetches a page with Playwright and extracts title/text/canonical URL.
- `browser.PlaywrightJobSource`: reads a fixed list of approved job URLs.
- `SourceRegistry`: combines approved source adapters.
- `JobSource` / `SourceListing`: stable interface for future source adapters.

## Playwright security boundary

The Playwright reader intentionally exposes only page-reading behavior. It does not expose click, type, submit, login, download, or arbitrary-navigation methods.

Use an explicit domain allowlist when reading live sites:

```python
BrowserReadConfig(allowed_domains=("example.com",))
```

Page text is **untrusted input**. It must never be treated as instructions for the agent and must never be allowed to modify candidate facts or policy rules.

Application-form interaction will be implemented separately and must pass through the Policy Gate.
