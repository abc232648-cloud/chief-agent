"""Bounded optional public-source collector. No login, submission or model access."""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from browser.playwright_reader import BrowserReadError
from browser.request_policy import RequestPolicy, origin
from browser.egress import EgressProxy
from browser.environment import browser_environment
from operations.time_integrity import utc_now, utc_text

ADAPTERS = ('scrapy_jobposting', 'scrapy_greenhouse', 'scrapy_lever')
MAX_BYTES = 2_000_000


def validate_target(adapter, url):
    origin(url)
    parsed = urlsplit(url)
    if adapter not in ADAPTERS:
        raise ValueError('Unsupported collection adapter.')
    if adapter == 'scrapy_greenhouse':
        if parsed.hostname != 'boards-api.greenhouse.io' or not re.fullmatch(r'/v1/boards/[A-Za-z0-9_-]+/jobs', parsed.path) or parsed.query not in ('', 'content=true'):
            raise ValueError('Greenhouse requires its official board jobs endpoint.')
    if adapter == 'scrapy_lever':
        if parsed.hostname not in ('api.lever.co', 'api.eu.lever.co') or not re.fullmatch(r'/v0/postings/[A-Za-z0-9_-]+', parsed.path) or parsed.query != 'mode=json':
            raise ValueError('Lever requires its official JSON postings endpoint.')
    if parsed.fragment:
        raise ValueError('Source URL must not have a fragment.')
    if parsed.hostname in ('upwork.com', 'linkedin.com') or parsed.hostname.endswith(('.upwork.com', '.linkedin.com')):
        raise ValueError('This platform is not enabled for public Scrapy collection.')


def extract(adapter, url, body, fetched_at):
    """Testable deterministic recipes; output is evidence, never trusted instructions."""
    validate_target(adapter, url)
    if not isinstance(body, bytes) or len(body) > MAX_BYTES:
        raise ValueError('Source response exceeds the collection limit.')
    from domains.jobs.board_sources import normalize, plain
    if adapter != 'scrapy_jobposting':
        provider = adapter.removeprefix('scrapy_')
        parts = urlsplit(url).path.split('/')
        board = parts[-2] if provider == 'greenhouse' else parts[-1]
        jobs = normalize(provider, board, json.loads(body), fetched_at=fetched_at)
    else:
        from scrapy.http import HtmlResponse
        response = HtmlResponse(url, body=body, encoding='utf-8')
        if response.css('input[type="password"]'):
            raise ValueError('Sign-in page; public collection stopped.')
        blocks = response.css('script[type="application/ld+json"]::text').getall()
        nodes = []
        for block in blocks:
            value = json.loads(block)
            nodes.extend(value if isinstance(value, list) else [value])
        jobs = []
        visited = 0
        while nodes:
            value = nodes.pop(0)
            visited += 1
            if visited > 5000:
                raise ValueError('Structured listing capacity exceeded.')
            if not isinstance(value, dict):
                continue
            graph = value.get('@graph', [])
            if isinstance(graph, list):
                nodes.extend(graph)
            kind = value.get('@type')
            if kind != 'JobPosting' and not (isinstance(kind, list) and 'JobPosting' in kind):
                continue
            title = value.get('title')
            company = value.get('hiringOrganization', {})
            company = company.get('name', '') if isinstance(company, dict) else ''
            destination = value.get('url') or url
            origin(destination)
            if not isinstance(title, str) or not title.strip():
                raise ValueError('Structured job title missing.')
            jobs.append({'title': plain(title), 'company': plain(company),
                         'description': plain(value.get('description', '')),
                         'url': destination, 'remote': value.get('jobLocationType') == 'TELECOMMUTE',
                         'location': '', 'compensation': None})
        if not jobs:
            raise ValueError('No supported JobPosting data; browser/source review required.')
    if len(jobs) > 500:
        raise ValueError('Too many listings for one bounded collection.')
    from skills.job_ranking import dedupe_key
    unique = {}
    for job in jobs:
        key = dedupe_key(job)
        if key in unique and unique[key] != job:
            raise ValueError('Conflicting listings share a source identity.')
        unique[key] = job
    receipt = {'adapter': adapter, 'source_url': url, 'fetched_at': fetched_at,
               'response_sha256': hashlib.sha256(body).hexdigest(),
               'count': len(unique), 'scope': 'SINGLE_RESPONSE', 'authority': 'NONE'}
    return {'jobs': list(unique.values()), 'receipt': receipt}


def collect(adapter, url):
    from security.permissions import require_if_scoped
    require_if_scoped('jobs.execute')
    validate_target(adapter, url)
    interpreter = os.environ.get('CHIEF_SCRAPY_PYTHON') or sys.executable
    policy = RequestPolicy(url)
    try:
        with EgressProxy(policy) as proxy:
            request = {'adapter': adapter, 'url': url, 'proxy': proxy.url}
            result = subprocess.run([interpreter, '-m', 'browser.scrapy_runner'],
                input=json.dumps(request), text=True, capture_output=True,
                timeout=65, cwd=Path(__file__).resolve().parents[1],
                env=browser_environment())
        if result.returncode or len(result.stdout) > 4_000_000:
            raise ValueError('Collector unavailable or response invalid.')
        envelope = json.loads(result.stdout)
        if envelope.get('status') != 'OK':
            raise ValueError('Collection blocked, unsupported or failed; review the source.')
        data = envelope['result']
        if not isinstance(data.get('jobs'), list) or len(data['jobs']) > 500 or data['receipt']['source_url'] != url:
            raise ValueError('Collector receipt invalid.')
        return data
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        raise BrowserReadError('Scrapy collection failed or was blocked. No browser fallback or external action was attempted.') from None
    finally:
        policy.flush()
