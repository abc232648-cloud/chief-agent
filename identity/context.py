from contextlib import contextmanager
from contextvars import ContextVar
_human=ContextVar('chief_authenticated_human',default=None)

def current_human():return _human.get()

@contextmanager
def human_context(principal):
    marker=_human.set(principal)
    try:yield principal
    finally:_human.reset(marker)
