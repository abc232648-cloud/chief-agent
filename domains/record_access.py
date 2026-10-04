"""Explicit legacy agent storage grants, separate from human workspace access.

Farm's existing worker may use soil tests. New human journals, role assignments,
finances and photos never inherit that worker's broad legacy records capability.
An added Farm record kind is private by default until explicitly qualified here.
"""
from types import MappingProxyType

LEGACY_RECORD_KINDS = MappingProxyType({'farming': frozenset({'soil_test'})})


def allowed_kinds(domain):
    return LEGACY_RECORD_KINDS.get(domain)
