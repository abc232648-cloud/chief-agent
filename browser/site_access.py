"""User-managed, domain-scoped browser sessions. Passwords never enter the API."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlparse


def site_url(value):
    parsed = urlparse(str(value).strip())
    host = (parsed.hostname or '').lower().rstrip('.')
    if parsed.scheme != 'https' or not host or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Enter a public HTTPS website URL without credentials or a custom port.')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError('Enter a website domain, not an IP address.')
    if '.' not in host or host.endswith(('.localhost', '.local', '.internal')):
        raise ValueError('Enter a public website domain.')
    return parsed._replace(fragment='').geturl(), host.removeprefix('www.')


class SiteAccess:
    def __init__(self, store):
        self.store = store
        self.folder = store.path.resolve().parent / 'site-sessions'
        if getattr(store,'operational',False):return
        with store._connect() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS site_access (
                domain TEXT PRIMARY KEY, url TEXT NOT NULL, name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'DISABLED', resume_at REAL,
                revision INTEGER NOT NULL DEFAULT 0, login_deadline REAL,
                message TEXT NOT NULL DEFAULT '', dismissed INTEGER NOT NULL DEFAULT 0,
                last_reason TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL)''')

    def path(self, domain):
        return self.folder / (hashlib.sha256(domain.encode()).hexdigest() + '.json')

    def get(self, domain):
        with self.store._connect() as con:
            con.execute("UPDATE site_access SET status='ACTIVE',resume_at=NULL,updated_at=? WHERE domain=? AND status='PAUSED' AND resume_at IS NOT NULL AND resume_at<=?", (time.time(), domain, time.time()))
            con.execute("UPDATE site_access SET status='LOGIN_REQUIRED',message='Sign-in window expired. Open it again.',updated_at=? WHERE domain=? AND status IN ('SIGNING_IN','SAVE_REQUESTED') AND login_deadline<?", (time.time(), domain, time.time()))
            row = con.execute('SELECT * FROM site_access WHERE domain=?', (domain,)).fetchone()
        return dict(row) if row else None

    def all(self):
        with self.store._connect() as con:
            domains = [r[0] for r in con.execute('SELECT domain FROM site_access ORDER BY name')]
        return [self.get(d) for d in domains]

    def for_url(self, url):
        host = (urlparse(url).hostname or '').lower()
        # Most specific user-controlled domain wins; a parent cannot bypass a paused child.
        return next((r for r in sorted(self.all(), key=lambda r: len(r['domain']), reverse=True)
                     if host == r['domain'] or host.endswith('.' + r['domain'])), None)

    def add(self, url, name=''):
        url, domain = site_url(url)
        with self.store._connect() as con:
            con.execute('INSERT INTO site_access(domain,url,name,updated_at) VALUES(?,?,?,?) ON CONFLICT(domain) DO UPDATE SET url=excluded.url,name=excluded.name,updated_at=excluded.updated_at',
                        (domain, url, str(name).strip()[:100] or domain, time.time()))
        return self.get(domain)

    def change(self, domain, action, hours=None):
        record = self.get(domain)
        if not record:
            raise ValueError('Add this website first.')
        if action == 'dismiss':
            with self.store._connect() as con:
                con.execute('UPDATE site_access SET dismissed=1 WHERE domain=?', (domain,))
            return self.get(domain)
        states = {'pause': 'PAUSED', 'resume': 'ACTIVE', 'revoke': 'REVOKED',
                  'public': 'ACTIVE', 'login': 'SIGNING_IN', 'save': 'SAVE_REQUESTED'}
        if action not in states:
            raise ValueError('Unknown website access action.')
        if action == 'save' and record['status'] != 'SIGNING_IN':
            raise ValueError('Open the sign-in window first.')
        if action == 'resume' and record['status'] != 'PAUSED':
            raise ValueError('Only paused access can be resumed. Authorize access again after revocation.')
        resume_at = None
        if action == 'pause' and hours is not None:
            hours = float(hours)
            if not 0 < hours <= 8760:
                raise ValueError('Suspension must be between zero and 8,760 hours.')
            resume_at = time.time() + hours * 3600
        with self.store._connect() as con:
            con.execute('UPDATE site_access SET status=?,resume_at=?,revision=revision+?,login_deadline=?,message=?,updated_at=? WHERE domain=?',
                        (states[action], resume_at, 0 if action == 'save' else 1,
                         time.time()+1800 if action in ('login', 'save') else None,
                         '', time.time(), domain))
            if action in ('revoke', 'public'):
                self.path(domain).unlink(missing_ok=True)
        from database.store_extensions import add_audit
        add_audit(self.store, 'source', 'Website access: '+action, actor='user', data={'domain': domain, 'resume_at': resume_at})
        return self.get(domain)

    def assert_active(self, domain, revision=None):
        row = self.get(domain)
        if not row or row['status'] != 'ACTIVE' or (revision is not None and row['revision'] != revision):
            from browser.playwright_reader import BrowserReadError
            raise BrowserReadError('Website access is not active. Review its access controls in Sources.')
        return row

    def state(self, domain):
        self.assert_active(domain)
        path = self.path(domain)
        return json.loads(path.read_text()) if path.exists() else None

    def start_login(self, domain):
        if os.name=='nt':raise ValueError('Website sign-in must currently run in the Ubuntu VM desktop; native Windows sign-in is not qualified.')
        from .environment import browser_environment
        env = browser_environment()
        desktop = subprocess.run(['systemctl', '--user', 'show-environment'], capture_output=True, text=True, timeout=5)
        for line in desktop.stdout.splitlines():
            key, _, value = line.partition('=')
            if key in ('DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR'):
                env[key] = value
        if not env.get('DISPLAY') and not env.get('WAYLAND_DISPLAY'):
            raise ValueError('Log into the Ubuntu VM desktop before opening a sign-in window.')
        row = self.change(domain, 'login')
        subprocess.Popen([sys.executable, '-m', 'browser.site_login', str(self.store.path.resolve()), domain, str(row['revision'])],
                         cwd=Path(__file__).resolve().parents[1], env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return row

    def recommend(self, url, reason):
        _, domain = site_url(url)
        row = self.get(domain) or self.add(url)
        reason = str(reason).strip()[:1000]
        if not reason or reason == row['last_reason']:
            return False
        with self.store._connect() as con:
            con.execute('UPDATE site_access SET last_reason=?,dismissed=0 WHERE domain=?', (reason, row['domain']))
        # Recommendation never enables access or undoes a pause/revocation.
        from notifications.site_alerts import send_site_alert
        send_site_alert(self.store, row['domain'], reason)
        return True


def main():
    import argparse
    from config.runtime import load_environment
    from database.store import Store, DEFAULT_DB
    load_environment()
    parser = argparse.ArgumentParser(description='Control saved website access; no passwords required.')
    parser.add_argument('action', choices=['list', 'pause', 'resume', 'revoke'])
    parser.add_argument('domain', nargs='?')
    parser.add_argument('--hours', type=float)
    args = parser.parse_args()
    access = SiteAccess(Store(os.environ.get('JOB_WORKER_DB', str(DEFAULT_DB))))
    result = access.all() if args.action == 'list' else access.change(args.domain, args.action, args.hours)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
