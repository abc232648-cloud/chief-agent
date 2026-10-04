# Optional public job collection

Implemented adapters under the existing jobs.execute permission:

- scrapy_jobposting: public HTML containing schema.org JobPosting JSON-LD, including @graph.
- scrapy_greenhouse: https://boards-api.greenhouse.io/v1/boards/BOARD/jobs?content=true
- scrapy_lever: https://api.lever.co/v0/postings/BOARD?mode=json (also api.eu.lever.co).

Set collector to the adapter name in an approved config/sources.json entry. The
omitted/default value is playwright. Existing sources are unchanged. Do not add a
source until its use is authorized. Logged-in managed sources retain the existing
Playwright path; these public collectors do not receive session cookies.

Example template, not an enabled real source:

```json
{"name":"Approved employer","domains":["boards-api.greenhouse.io"],
 "enabled":true,"read_only":true,"collector":"scrapy_greenhouse",
 "start_urls":["https://boards-api.greenhouse.io/v1/boards/BOARD/jobs?content=true"]}
```

Install requirements-scrapy.txt into a dedicated Python virtual environment and
set CHIEF_SCRAPY_PYTHON to its absolute interpreter path in Chief's service
environment. If omitted, Chief uses its own interpreter and therefore requires
Scrapy there. Missing dependencies cause a clear collection error, not fallback.
The separate worker also imports Chief's pure validation/normalization modules.
No runtime framework or model credentials are required by the collector.

Chief's existing discover_jobs action routes approved URLs by their source's
collector setting. The adapter is registered as a backend of jobs.execute, not a
new authority grant. Jobs enter the existing normalization, scam screening,
candidate matching, model analysis and deduplication pipeline. The collection
receipt records adapter, source, retrieval time and response digest in the audit.
Evidence is not proof that an employer or vacancy is legitimate.

Each invocation launches a fresh bounded subprocess through Chief's existing
public-address-pinning HTTPS proxy. It strips provider credentials and ambient
proxy settings, verifies TLS, fetches robots.txt first, honors disallow/crawl-delay,
disables redirects/retries/cookies, and reads one response. Robots fetch errors
fail closed; a 404 means no published robots file. Runtime timeout is 65 seconds,
response limit 2 MB, result limit 500 listings. No link following, login, CAPTCHA
handling, external submission, automatic browser fallback or automatic scheduler
is introduced. Partial/truncated/paginated feeds are not called complete snapshots.

This is a bounded first implementation, not a universal crawler. Custom HTML
layouts without JobPosting data require a tested recipe or the browser path.
Live publisher acceptance and native Windows execution remain OPEN. Fixture
transport tests exercise Scrapy itself, but are not live website tests.
