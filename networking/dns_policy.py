"""Chief-owned DNS and process-scoped resolver boundary.

The default secure mode resolves Chief hostnames with Quad9 DNS-over-HTTPS.
The optional ``system`` mode uses the host resolver only when selected explicitly.
Neither mode mutates /etc/resolv.conf, NetworkManager, Windows DNS settings, or
any other operating-system resolver configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
import socket
import threading
import time


@dataclass(frozen=True)
class DoHProvider:
    endpoint: str
    bootstrap_address: str


QUAD9_SECURE = DoHProvider('https://dns.quad9.net/dns-query', '9.9.9.9')
MODES = frozenset({'quad9', 'system'})
_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_POLICY_LOCK = threading.RLock()
_PROCESS_MODE: str | None = None


def validate_mode(mode):
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError('Chief DNS must be quad9 or system.')
    return mode


def selected_mode():
    return validate_mode(os.environ.get('CHIEF_DNS_MODE', 'quad9'))


def _system_getaddrinfo(host, port, *, family=0, type=0, proto=0, flags=0):
    # Preserve ordinary monkeypatch/test semantics when the Chief hook is not the
    # current resolver. Once the hook is installed, call the captured OS resolver
    # directly to avoid recursive entry into Chief policy in explicit system mode.
    resolver = _ORIGINAL_GETADDRINFO if socket.getaddrinfo is _chief_getaddrinfo else socket.getaddrinfo
    return resolver(host, port, family, type, proto, flags)


def resolve_addresses(host, port, *, mode=None, provider=QUAD9_SECURE):
    """Resolve once under the selected Chief DNS policy.

    Secure mode never reads system resolver configuration and never falls back to
    another resolver after timeout, NXDOMAIN, SERVFAIL, TLS, or transport errors.
    Absolute query names prevent search-suffix leakage. A shared lifetime bounds
    both A and AAAA queries.
    """
    mode = selected_mode() if mode is None else validate_mode(mode)
    try:
        return (str(ipaddress.ip_address(host)),)
    except ValueError:
        pass
    if mode == 'system':
        try:
            return tuple(dict.fromkeys(
                r[4][0] for r in _system_getaddrinfo(host, port, type=socket.SOCK_STREAM)
            ))
        except OSError:
            raise PermissionError('Chief System DNS resolution failed.') from None
    try:
        import dns.nameserver
        import dns.query
        import dns.resolver
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = [dns.nameserver.DoHNameserver(
            provider.endpoint,
            bootstrap_address=provider.bootstrap_address,
            verify=True,
            http_version=dns.query.HTTPVersion.H2,
        )]
        resolver.retry_servfail = False
        resolver.timeout = 3.0
        deadline = time.monotonic() + 5.0
        addresses = []
        for kind in ('A', 'AAAA'):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            answer = resolver.resolve(
                host.rstrip('.') + '.',
                kind,
                search=False,
                lifetime=remaining,
                raise_on_no_answer=False,
            )
            addresses.extend(str(item.address) for item in answer)
        if not addresses:
            raise ValueError('Empty DNS answer')
        return tuple(dict.fromkeys(addresses))
    except Exception:
        raise PermissionError(
            'Chief secure DNS blocked or could not resolve the destination; no fallback was used.'
        ) from None


def _permitted_public_address(value):
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast:
        return False
    if address.version == 6 and (
        address.ipv4_mapped
        or address.sixtofour
        or address.teredo
        or address in ipaddress.ip_network('64:ff9b::/96')
        or address in ipaddress.ip_network('64:ff9b:1::/48')
    ):
        return False
    return True


def public_addresses(host, port, *, mode=None):
    """Resolve a hostname and require every returned address to be public.

    Mixed public/private answers fail closed. Numeric loopback integrations should
    remain explicit numeric destinations and therefore do not need DNS at all.
    """
    text = str(host)
    if text.replace('.', '').isdigit():
        try:
            ipaddress.IPv4Address(text)
        except ValueError:
            raise PermissionError('Abbreviated numeric destinations are blocked.') from None
    addresses = resolve_addresses(text, port, mode=mode)
    if not addresses or not all(_permitted_public_address(value) for value in addresses):
        raise PermissionError('Destination is not exclusively public.')
    return addresses


def _numeric_host(host):
    try:
        return str(ipaddress.ip_address(host))
    except (TypeError, ValueError):
        return None


def _chief_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """socket.getaddrinfo replacement scoped to the current Chief process only."""
    if host is None:
        return _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)
    if isinstance(host, bytes):
        try:
            host = host.decode('ascii')
        except UnicodeDecodeError:
            raise socket.gaierror(socket.EAI_NONAME, 'Chief DNS requires an ASCII hostname.') from None
    text = str(host).rstrip('.')
    numeric = _numeric_host(text)
    if numeric is not None:
        return _ORIGINAL_GETADDRINFO(numeric, port, family, type, proto, flags | socket.AI_NUMERICHOST)
    # Explicit loopback hostnames are local-only and do not need external DNS.
    if text.lower() == 'localhost':
        return _ORIGINAL_GETADDRINFO(text, port, family, type, proto, flags)

    with _POLICY_LOCK:
        mode = _PROCESS_MODE or selected_mode()
    addresses = public_addresses(text, port, mode=mode)
    results = []
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if family == socket.AF_INET and parsed.version != 4:
            continue
        if family == socket.AF_INET6 and parsed.version != 6:
            continue
        try:
            entries = _ORIGINAL_GETADDRINFO(
                address,
                port,
                family,
                type,
                proto,
                flags | socket.AI_NUMERICHOST,
            )
        except OSError:
            continue
        for entry in entries:
            if entry not in results:
                results.append(entry)
    if not results:
        raise socket.gaierror(socket.EAI_NONAME, 'Chief DNS returned no address for the requested family.')
    return results


def install_process_policy(mode=None):
    """Install Chief DNS for this Python process without changing host DNS."""
    global _PROCESS_MODE
    mode = selected_mode() if mode is None else validate_mode(mode)
    with _POLICY_LOCK:
        if socket.getaddrinfo not in (_ORIGINAL_GETADDRINFO, _chief_getaddrinfo):
            raise RuntimeError('Chief DNS cannot safely replace an unknown process resolver hook.')
        if _PROCESS_MODE is not None and _PROCESS_MODE != mode:
            raise PermissionError('Conflicting Chief DNS mode in this process.')
        _PROCESS_MODE = mode
        socket.getaddrinfo = _chief_getaddrinfo
    return mode


def ensure_process_policy(mode=None):
    """Ensure all ordinary Python hostname lookups in this Chief process use policy."""
    expected = selected_mode() if mode is None else validate_mode(mode)
    with _POLICY_LOCK:
        if socket.getaddrinfo is _chief_getaddrinfo:
            if _PROCESS_MODE != expected:
                raise PermissionError('Conflicting Chief DNS mode in this process.')
            return expected
    return install_process_policy(expected)


def process_policy_mode():
    with _POLICY_LOCK:
        return _PROCESS_MODE if socket.getaddrinfo is _chief_getaddrinfo else None


def _reset_process_policy_for_tests():
    """Test-only cleanup for the process-global resolver hook."""
    global _PROCESS_MODE
    with _POLICY_LOCK:
        if socket.getaddrinfo is _chief_getaddrinfo:
            socket.getaddrinfo = _ORIGINAL_GETADDRINFO
        _PROCESS_MODE = None
