"""Compatibility surface for Chief's shared DNS policy.

The implementation now lives in :mod:`networking.dns_policy` because DNS policy
applies to every Chief-owned Python network client, not only browser traffic.
"""
from networking.dns_policy import (
    DoHProvider,
    MODES,
    QUAD9_SECURE,
    ensure_process_policy,
    install_process_policy,
    process_policy_mode,
    public_addresses,
    resolve_addresses,
    selected_mode,
    validate_mode,
)

__all__ = [
    'DoHProvider',
    'MODES',
    'QUAD9_SECURE',
    'ensure_process_policy',
    'install_process_policy',
    'process_policy_mode',
    'public_addresses',
    'resolve_addresses',
    'selected_mode',
    'validate_mode',
]
