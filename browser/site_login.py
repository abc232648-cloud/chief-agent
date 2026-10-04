"""Visible browser controlled only by the user, isolated from the AI worker."""
import json
import os
import sys
import time
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from database.store import Store
from browser.site_access import SiteAccess


def matches(host, domain):
    return host == domain or host.endswith('.' + domain)


def login(db, domain, revision):
    from playwright.sync_api import sync_playwright
    access = SiteAccess(Store(db))
    row = access.get(domain)
    if not row or row['revision'] != revision or row['status'] != 'SIGNING_IN':
        return
    try:
        with sync_playwright() as p:
            from .environment import browser_environment
            from .runtime import launch_options
            browser = p.chromium.launch(headless=False,env=browser_environment(), **launch_options())
            try:
                context = browser.new_context(accept_downloads=False)
                page = context.new_page()
                page.goto(row['url'], wait_until='domcontentloaded', timeout=30000)
                while browser.is_connected():
                    row = access.get(domain)
                    if row['revision'] != revision or row['status'] not in ('SIGNING_IN', 'SAVE_REQUESTED'):
                        return
                    if row['status'] == 'SAVE_REQUESTED':
                        state = context.storage_state()
                        state['cookies'] = [c for c in state['cookies'] if matches(c['domain'].lstrip('.'), domain)]
                        state['origins'] = [o for o in state['origins'] if matches(urlparse(o['origin']).hostname or '', domain)]
                        access.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
                        os.chmod(access.folder, 0o700)
                        with access.store._connect() as con:
                            con.execute('BEGIN IMMEDIATE')
                            current = con.execute('SELECT revision,status FROM site_access WHERE domain=?', (domain,)).fetchone()
                            if tuple(current) != (revision, 'SAVE_REQUESTED'):
                                return
                            path = access.path(domain)
                            from operations.backup import _plain
                            _plain(access.folder)
                            fd,temporary=tempfile.mkstemp(prefix='.session-',dir=access.folder)
                            try:
                                with os.fdopen(fd,'w') as stream:
                                    json.dump(state,stream);stream.flush();os.fsync(stream.fileno())
                                os.replace(temporary,path)
                            finally:Path(temporary).unlink(missing_ok=True)
                            con.execute("UPDATE site_access SET status='ACTIVE',message=?,login_deadline=NULL WHERE domain=?", ('Session saved. Access depends on the website keeping it valid.', domain))
                        return
                    page.wait_for_timeout(500)
            finally:
                browser.close()
    except Exception:
        # Browser errors can include redirected URLs/tokens. Keep diagnostics generic.
        with access.store._connect() as con:
            con.execute("UPDATE site_access SET status='LOGIN_REQUIRED',message=? WHERE domain=? AND revision=? AND status IN ('SIGNING_IN','SAVE_REQUESTED')",
                        ('Sign-in window closed or could not load. Open it again from Sources.', domain, revision))


if __name__ == '__main__':
    login(sys.argv[1], sys.argv[2], int(sys.argv[3]))
