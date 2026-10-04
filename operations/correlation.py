"""Scoped reference metadata only. Never an authorization credential."""
from contextlib import contextmanager
from contextvars import ContextVar
import re

_references=ContextVar('chief_correlation',default=None)
_allowed={'request_id','ledger_correlation_id','command_id','action_id'}


def references():
    return dict(_references.get() or {})


@contextmanager
def scope(**values):
    if set(values)-_allowed:raise ValueError('Unsupported correlation reference.')
    for key,value in values.items():
        if key.endswith('_id') and key in {'command_id','action_id'}:
            if type(value) is not int or value<1:raise ValueError('Invalid work reference.')
        elif not isinstance(value,str) or not re.fullmatch('[0-9a-f]{32}',value):
            raise ValueError('Invalid generated correlation reference.')
    token=_references.set({**references(),**values})
    try:yield
    finally:_references.reset(token)
