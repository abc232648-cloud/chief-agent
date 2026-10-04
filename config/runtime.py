"""Runtime environment loading for local, non-secret configuration.

Secrets may be supplied through a local .env file (ignored by Git) or the
process environment. Existing process environment variables always win.
"""
from __future__ import annotations

from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load_environment(dotenv_path: str | Path | None = None) -> None:
    """Load local environment values without overriding explicit process env."""
    if os.environ.get('CHIEF_SERVICE_CONFIGURED')=='1' or os.environ.get('CHIEF_INSTANCE_MODE','').lower()=='production':
        if dotenv_path is not None:raise PermissionError('Production configuration and secret references must be explicit; .env loading is disabled.')
        return
    path = Path(dotenv_path) if dotenv_path is not None else ROOT / ".env"
    load_dotenv(dotenv_path=path, override=False)
