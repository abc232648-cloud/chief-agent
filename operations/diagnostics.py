"""Bounded, allowlisted diagnostics. Audit/history retention is separate."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
from operations.backup import _plain

MAX_BYTES=1024*1024
BACKUPS=3
CODES={'APPLICATION_ERROR','CLIENT_DISCONNECTED','PROXY_HEADER_REJECTED','SERVICE_READY','SERVICE_STOPPED'}
AGENT_OUTCOMES={'BUILTIN','LIVE','BLOCKED','FAILED'}


def agent_trace(correlation, outcome, elapsed_ms, model):
    """Closed metadata vocabulary; never accept prompts, provider text or keys."""
    if not isinstance(correlation,str) or not re.fullmatch(r'[0-9a-f]{32}',correlation):
        raise ValueError('Invalid trace reference.')
    if outcome not in AGENT_OUTCOMES or model not in {'builtin','farming.qwen','none'}:
        raise ValueError('Invalid trace category.')
    if type(elapsed_ms) is not int or not 0<=elapsed_ms<=86400000:
        raise ValueError('Invalid trace duration.')
    logging.getLogger('chief.http').warning('FARM_GUIDANCE_%s correlation=%s elapsed_ms=%d model=%s',outcome,correlation,elapsed_ms,model)


def _allowed(record):
    # Apply at both the logger and file sink: handlers attached directly by an
    # embedding application must not receive rejected private payloads either.
    try:
        text=record.getMessage()
        if record.exc_info:return False
        normal=re.fullmatch(r'[A-Z_]+ correlation=[0-9a-f]{32}',text) and text.split()[0] in CODES
        trace=re.fullmatch(r'FARM_GUIDANCE_(BUILTIN|LIVE|BLOCKED|FAILED) correlation=[0-9a-f]{32} elapsed_ms=([0-9]{1,8}) model=(builtin|farming\.qwen|none)',text)
        return bool(normal or (trace and int(trace.group(2))<=86400000))
    except (TypeError,ValueError):
        return False


class SafeFilter(logging.Filter):
    def filter(self,record):
        return _allowed(record)


class SafeHandler(RotatingFileHandler):
    def emit(self,record):
        if not _allowed(record):return
        super().emit(record)


def configure(root,component):
    if component not in {'dashboard','worker','scheduler'}:raise ValueError('Unknown component.')
    root=Path(root);_plain(root)
    directory=root/'diagnostics'
    directory.mkdir(mode=0o700,exist_ok=True);_plain(directory)
    path=directory/(component+'.log')
    for candidate in [path,*(Path(str(path)+'.'+str(i)) for i in range(1,BACKUPS+1))]:
        if candidate.exists() or candidate.is_symlink():_plain(candidate,file=True)
    handler=SafeHandler(path,maxBytes=MAX_BYTES,backupCount=BACKUPS,encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger=logging.getLogger('chief.http' if component=='dashboard' else 'chief.'+component)
    # Ancestor handlers must never receive messages rejected by our safe sink.
    logger.propagate=False
    if not any(isinstance(f,SafeFilter) for f in logger.filters):logger.addFilter(SafeFilter())
    logger.setLevel(logging.WARNING);logger.addHandler(handler)
    return logger,handler
