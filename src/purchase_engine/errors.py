"""Exception hierarchy.

Everything the engine raises on purpose derives from :class:`PurchaseEngineError`,
so a caller can ``except PurchaseEngineError`` and know it was us, not a bug.
"""

from __future__ import annotations


class PurchaseEngineError(Exception):
    """Base class for every deliberate error raised by the engine."""


class ConfigError(PurchaseEngineError):
    """`config/engine.yml` is missing a key, or a value is out of range."""


class DataSourceError(PurchaseEngineError):
    """The parser workbook is missing, unreadable, or missing an expected sheet."""


class IncomingSourceError(PurchaseEngineError):
    """The configured incoming-stock source cannot be built or read."""
