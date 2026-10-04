"""One default-deny policy for every automated browser context."""
from contextlib import contextmanager
import ipaddress
import threading
import uuid
from urllib.parse import urlsplit
from .environment import browser_environment
from .egress import EgressProxy
from .runtime import launch_options


class BrowserPolicyBlocked(ValueError):pass


def origin(url):
    try:
        parsed=urlsplit(url);host=(parsed.hostname or '').lower()
        if (parsed.scheme!='https' or not host or parsed.username or parsed.password
                or parsed.port not in (None,443) or host.endswith('.') or '%' in host):raise ValueError()
        host=host.encode('idna').decode('ascii')
        try:
            address=ipaddress.ip_address(host)
            if not address.is_global or address.is_multicast:raise BrowserPolicyBlocked('Private network destinations are blocked.')
        except ValueError as exc:
            if isinstance(exc,BrowserPolicyBlocked):raise
            if '.' not in host or host.endswith(('.localhost','.local','.internal')):raise ValueError()
        return ('https',host,443)
    except (ValueError,UnicodeError):
        raise BrowserPolicyBlocked('Automated browsing requires an approved public HTTPS origin.') from None


class RequestPolicy:
    def __init__(self,url,*,resource_origins=(),revision_check=None,store=None):
        self.primary=origin(url);self.origins={self.primary,*(origin(u) for u in resource_origins)}
        self.phase='READ';self.revision_check=revision_check;self.store=store
        self.correlation_id=uuid.uuid4().hex;self.events=[];self.lock=threading.Lock()

    def block(self,code):
        # Proxy threads queue bounded fixed-code events; SQLite audit is flushed by the caller.
        with self.lock:
            if len(self.events)<100:self.events.append(code)

    def flush(self):
        if self.store:
            from database.store_extensions import add_audit
            for code in set(self.events):
                add_audit(self.store,'security','Browser request blocked',actor='browser',status='BLOCKED',
                          data={'reason_code':code,'correlation_id':self.correlation_id})
        elif self.events:
            import logging
            for code in set(self.events):
                logging.getLogger('chief.browser.security').warning('Browser request blocked: %s correlation=%s',code,self.correlation_id)

    def allows(self,url,method,*,navigation=False):
        if self.phase=='FILL':return False
        try:target=origin(url)
        except BrowserPolicyBlocked:return False
        if target not in ({self.primary} if navigation else self.origins):return False
        return method in {'GET','HEAD'} or (self.phase=='SUBMIT' and target==self.primary and method=='POST')

    def install(self,context):
        # These APIs are unnecessary for Job reading/form preparation and may use
        # transports outside ordinary HTTP routing. No page can re-enable them.
        context.add_init_script(script="for(const name of ['RTCPeerConnection','webkitRTCPeerConnection','WebTransport','Worker','SharedWorker'])Object.defineProperty(window,name,{value:undefined,writable:false,configurable:false});")
        def guard(route):
            try:
                if self.revision_check:self.revision_check()
                request=route.request
                if not self.allows(request.url,request.method,navigation=request.is_navigation_request()):
                    self.block('REQUEST_POLICY_DENIED');route.abort();return
                # Some browser redirects bypass route interception. The independent
                # CONNECT proxy still checks every destination and pins its public IP.
                if request.redirected_from is not None:
                    self.block('REDIRECT_CHAIN_DENIED');route.abort();return
                route.fallback()
            except Exception:
                self.block('REQUEST_VALIDATION_FAILED');route.abort()
        context.route('**/*',guard)
        # A routed socket does not connect upstream unless connect_to_server is
        # called. Do not close synchronously inside the handshake callback.
        def websocket(ws):self.block('WEBSOCKET_DENIED')
        context.route_web_socket('**/*',websocket)


@contextmanager
def controlled_browser(playwright,url,*,storage_state=None,resource_origins=(),revision_check=None,store=None):
    policy=RequestPolicy(url,resource_origins=resource_origins,revision_check=revision_check,store=store)
    with EgressProxy(policy) as proxy:
        browser=None
        try:
            browser=playwright.chromium.launch(headless=True,env=browser_environment(), **launch_options(),
                proxy={'server':proxy.url,'bypass':'<-loopback>'},
                args=['--disable-quic','--force-webrtc-ip-handling-policy=disable_non_proxied_udp','--disable-dns-prefetch'])
            context=browser.new_context(storage_state=storage_state,accept_downloads=False,service_workers='block')
            policy.install(context)
            yield context,policy
        finally:
            if browser:browser.close()
            policy.flush()
