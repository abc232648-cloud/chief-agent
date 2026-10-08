"""Fail-closed runtime gate for Farm HTTP operations.

Test/preview remain available for qualification. Production Farm access requires a
validated service launch to opt in explicitly and requires the same-host TLS proxy
contract. This module does not approve or enable a real deployment by itself.
"""
import ipaddress
import os


def require_farm_runtime():
    mode = os.environ.get('CHIEF_INSTANCE_MODE', '').strip().lower()
    if mode in {'test', 'preview'}:
        return
    if mode != 'production':
        raise PermissionError('Farm operations require an explicit supported instance mode.')
    if os.environ.get('CHIEF_SERVICE_CONFIGURED') != '1':
        raise PermissionError('Production Farm operations require a validated service configuration.')
    if os.environ.get('CHIEF_FARM_PRODUCTION') != 'ENABLED':
        raise PermissionError('Farm production operations are disabled by instance configuration.')
    proxy = os.environ.get('CHIEF_TRUSTED_PROXY', '').strip()
    try:
        trusted = ipaddress.ip_address(proxy)
    except ValueError as exc:
        raise PermissionError('Production Farm operations require an explicit same-host TLS proxy.') from exc
    if not trusted.is_loopback:
        raise PermissionError('Production Farm operations require an explicit same-host TLS proxy.')
    host = os.environ.get('DASHBOARD_HOST', '127.0.0.1').strip()
    try:
        bind = ipaddress.ip_address('127.0.0.1' if host == 'localhost' else host)
    except ValueError as exc:
        raise PermissionError('Production Farm backend must bind to loopback behind TLS.') from exc
    if not bind.is_loopback:
        raise PermissionError('Production Farm backend must bind to loopback behind TLS.')
