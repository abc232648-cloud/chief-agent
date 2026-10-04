"""Optional additional live revalidation for an identified queued approval.

Direct domain-owned executors retain their explicit approval/policy contracts;
this context never grants permission and cannot replace those checks.
"""
from contextvars import ContextVar
from contextlib import contextmanager

_validator=ContextVar('chief_action_revalidator',default=None)


def revalidate():
    validator=_validator.get()
    if validator is not None:validator()


@contextmanager
def approval_scope(validator):
    token=_validator.set(validator)
    try:yield
    finally:_validator.reset(token)
