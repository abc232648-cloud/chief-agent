"""Browser boundary. Ingestion is read-only; application actions remain gated elsewhere."""

from .playwright_reader import BrowserReadConfig, BrowserReadError, PlaywrightJobSource, PlaywrightReader

__all__ = ["BrowserReadConfig", "BrowserReadError", "PlaywrightJobSource", "PlaywrightReader"]
