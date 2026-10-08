"""Validated, non-secret HTTPS deployment contract.

The actual TLS terminator is installed and qualified during deployment. Chief only
accepts a same-host loopback proxy and never treats forwarded identity/host data as
authority.
"""
import ipaddress
import re

_LABEL = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')


def trusted_loopback(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Production requires an explicit trusted TLS proxy address.')
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise ValueError('Trusted TLS proxy must be one explicit IP address.') from exc
    if not address.is_loopback:
        raise ValueError('Trusted TLS proxy must be on the same-host loopback interface.')
    return address.compressed


def public_hostname(value):
    if not isinstance(value, str):
        raise ValueError('Production public host is required.')
    host = value.strip().lower().rstrip('.')
    if not host or len(host) > 253 or '/' in host or ':' in host or '@' in host:
        raise ValueError('Production public host must be a DNS hostname without scheme, path or port.')
    if host in {'localhost', 'localhost.localdomain'}:
        raise ValueError('Production public host cannot be localhost.')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError('Production public host must use a certificate hostname, not a literal IP address.')
    labels = host.split('.')
    if len(labels) < 2 or any(not _LABEL.fullmatch(label) for label in labels):
        raise ValueError('Production public host is not a valid DNS hostname.')
    return host


def contract(public_host, dashboard_port, trusted_proxy):
    host = public_hostname(public_host)
    proxy = trusted_loopback(trusted_proxy)
    if type(dashboard_port) is not int or not 1 <= dashboard_port <= 65535:
        raise ValueError('Invalid dashboard port.')
    return {
        'public_origin': f'https://{host}',
        'public_host': host,
        'application_protocol': 'HTTPS_ONLY',
        'backend_listener': f'127.0.0.1:{dashboard_port}',
        'trusted_proxy': proxy,
        'proxy_requirements': [
            'Terminate certificate-verified TLS before forwarding to Chief.',
            'Forward only to the loopback backend listener.',
            'Preserve the original Host header.',
            'Overwrite X-Forwarded-Proto with https.',
            'Do not expose the loopback backend to other hosts or containers.',
            'Do not forward client identity headers as authority.',
        ],
        'response_security': {
            'Strict-Transport-Security': 'max-age=31536000',
            'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY',
            'Referrer-Policy': 'no-referrer',
            'Permissions-Policy': 'camera=(self), microphone=(), geolocation=()',
        },
    }
