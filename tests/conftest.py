"""Test HTTP servers use disposable storage and OS-assigned ports, never 8765."""
import importlib.util
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
import pytest

@pytest.fixture(autouse=True)
def qualified_acceptance_browser(monkeypatch):
    """When selected by the acceptance runner, all Chromium fixtures use that build."""
    import os
    if not os.environ.get('CHIEF_BROWSER_RUNTIME'):
        return
    from playwright.sync_api import BrowserType
    from browser.runtime import launch_options
    original = BrowserType.launch
    def launch(self, *args, **kwargs):
        if self.name == 'chromium':
            kwargs.update(launch_options())
        return original(self, *args, **kwargs)
    monkeypatch.setattr(BrowserType, 'launch', launch)

@pytest.fixture(autouse=True)
def explicit_isolated_instance(tmp_path,monkeypatch):
    import json
    (tmp_path/'.chief-isolated-development.json').write_text(json.dumps({'purpose':'ISOLATED_DEVELOPMENT'}))
    # Every test starts from a development/test baseline. Individual tests may
    # opt into production/service flags afterwards through their own monkeypatch.
    # This prevents process-global environment writes in one regression from
    # contaminating a later, otherwise isolated test.
    for name in ('CHIEF_SERVICE_CONFIGURED','CHIEF_FARM_PRODUCTION','CHIEF_TRUSTED_PROXY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','test')
    monkeypatch.setenv('CHIEF_ISOLATED_ROOT',str(tmp_path))
    monkeypatch.setenv('CHIEF_STATE_ROOT',str(tmp_path))
    monkeypatch.setenv('JOB_WORKER_DB',str(tmp_path/'worker.db'))

@pytest.fixture(autouse=True)
def portable_ci_secret_backend(monkeypatch):
    """Provide an authenticated, non-plaintext secret backend on hosted CI only.

    Chief's Linux production backend remains systemd-creds and remains fail-closed.
    GitHub hosted runners do not provide the host-bound credential key required by
    that backend, so the full regression suite may explicitly request this
    process-local AES-GCM test double. The real OS backend remains a host
    acceptance concern and production code is not given a fallback.
    """
    import os
    if os.environ.get('CHIEF_TEST_SECRET_BACKEND') != 'portable-aead':
        return
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from private_secrets import imported

    key=AESGCM.generate_key(bit_length=256)
    cipher=AESGCM(key)
    magic=b'chief-test-aead-v1\0'
    aad=b'chief-model-key'

    def protected(operation,value):
        if operation == 'encrypt':
            if not isinstance(value,(bytes,bytearray)):
                raise ValueError('Test credential must be bytes.')
            nonce=os.urandom(12)
            return magic+nonce+cipher.encrypt(nonce,bytes(value),aad)
        if operation == 'decrypt':
            blob=bytes(value)
            offset=len(magic)
            if not blob.startswith(magic) or len(blob) <= offset+12:
                raise ValueError('Invalid test credential envelope.')
            nonce=blob[offset:offset+12]
            try:
                return cipher.decrypt(nonce,blob[offset+12:],aad)
            except Exception as exc:
                raise ValueError('Invalid test credential envelope.') from exc
        raise ValueError('Unsupported test credential operation.')

    monkeypatch.setattr(imported,'_systemd',protected)

@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv('JOB_WORKER_DB', str(tmp_path/'worker.db'))
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('isolated_dashboard',root/'dashboard_app.py')
    app=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    from tests.checkpoint_f_fixture import secure_store
    from identity.service import IdentityService
    credentials={}
    def authenticate_store(store):
        raw,principal=secure_store(store);credentials.update(raw=raw,principal=principal,csrf=IdentityService.csrf(raw))
    authenticate_store(app.STORE)
    app.ROOT=tmp_path
    (tmp_path/'candidate/cv_variants').mkdir(parents=True)
    from application.http_transport import create_dashboard_server, close_dashboard_server, TransportSettings
    server=create_dashboard_server(app.Handler,TransportSettings(port=0))
    import urllib.request
    from urllib.parse import urlparse
    origin=f'http://127.0.0.1:{server.effective_port}'
    class AuthHeaders(urllib.request.BaseHandler):
        def http_request(self,request):
            if urlparse(request.full_url).netloc==urlparse(origin).netloc:
                if not request.has_header('Cookie'):request.add_header('Cookie','chief_session='+credentials['raw'])
                if not request.has_header('X-chief-csrf'):request.add_header('X-Chief-CSRF',credentials['csrf'])
                if not request.has_header('Origin'):request.add_header('Origin',origin)
            return request
    monkeypatch.setattr(urllib.request,'_opener',urllib.request.build_opener(AuthHeaders()))
    # Existing browser regressions receive real authenticated sessions if launch works.
    from playwright.sync_api import Browser
    new_page=Browser.new_page
    def authenticated_page(browser,*args,**kwargs):
        page=new_page(browser,*args,**kwargs)
        page.context.add_cookies([{'name':'chief_session','value':credentials['raw'],'url':origin,'httpOnly':True,'sameSite':'Strict'}])
        return page
    monkeypatch.setattr(Browser,'new_page',authenticated_page)
    new_context=Browser.new_context
    def authenticated_context(browser,*args,**kwargs):
        context=new_context(browser,*args,**kwargs)
        context.add_cookies([{'name':'chief_session','value':credentials['raw'],'url':origin,'httpOnly':True,'sameSite':'Strict'}])
        return context
    monkeypatch.setattr(Browser,'new_context',authenticated_context)
    thread=Thread(target=server.run,daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(app=app,store=app.STORE,url=origin,root=root,credentials=credentials,secure_store=authenticate_store)
    finally:
        close_dashboard_server(server); thread.join(timeout=5)
        assert not thread.is_alive()

@pytest.fixture(autouse=True)
def isolate_external_notification_delivery(monkeypatch):
    # Regression runs never send email or desktop notifications to real users.
    monkeypatch.setattr("notifications.channels.SmtpEmailChannel._send",lambda *a,**kw:None)
    monkeypatch.setattr("notifications.desktop.DesktopNotificationChannel._send",lambda *a,**kw:True)
