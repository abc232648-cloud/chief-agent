"""Chief-owned DNS only. Never mutates host DNS or installs global socket hooks."""
from dataclasses import dataclass
import ipaddress
import os
import socket
import time


@dataclass(frozen=True)
class DoHProvider:
    endpoint: str
    bootstrap_address: str


# Provider metadata is separate from transport and destination policy.
QUAD9_SECURE = DoHProvider('https://dns.quad9.net/dns-query', '9.9.9.9')
MODES = frozenset({'quad9', 'system'})


def validate_mode(mode):
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError('Chief DNS must be quad9 or system.')
    return mode


def selected_mode():
    return validate_mode(os.environ.get('CHIEF_DNS_MODE', 'quad9'))


def resolve_addresses(host, port, *, mode=None, provider=QUAD9_SECURE):
    """Resolve once; callers must validate and pin addresses before connecting.

    No timeout/NXDOMAIN/SERVFAIL/TLS-error fallback to another resolver. Absolute
    names avoid search suffix leakage. A shared lifetime bounds A + AAAA queries.
    """
    mode = selected_mode() if mode is None else validate_mode(mode)
    try:
        return (str(ipaddress.ip_address(host)),)
    except ValueError:
        pass
    if mode == 'system':
        try:
            return tuple(dict.fromkeys(r[4][0] for r in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
        except OSError:
            raise PermissionError('Chief System DNS resolution failed.') from None
    try:
        import dns.nameserver
        import dns.query
        import dns.resolver
        # configure=False prevents reading resolv.conf or using system fallback.
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = [dns.nameserver.DoHNameserver(
            provider.endpoint, bootstrap_address=provider.bootstrap_address,
            verify=True, http_version=dns.query.HTTPVersion.H2)]
        resolver.retry_servfail = False
        resolver.timeout = 3.0
        deadline = time.monotonic() + 5.0
        addresses = []
        for kind in ('A', 'AAAA'):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            answer = resolver.resolve(host.rstrip('.') + '.', kind, search=False,
                                      lifetime=remaining, raise_on_no_answer=False)
            addresses.extend(str(item.address) for item in answer)
        if not addresses:
            raise ValueError('Empty DNS answer')
        return tuple(dict.fromkeys(addresses))
    except Exception:
        # DNS failures/blocks are terminal; do not log domains or query contents.
        raise PermissionError('Chief secure DNS blocked or could not resolve the destination; no fallback was used.') from None
