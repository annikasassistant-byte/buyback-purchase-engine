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


class StoreError(PurchaseEngineError):
    """A :class:`~purchase_engine.domain.ports.RecommendationStore` adapter is
    misconfigured - e.g. ``--postgres``/the API given without ``DATABASE_URL``
    set, or the optional driver for one isn't installed."""


class NotFoundError(PurchaseEngineError):
    """The API was asked for a run/product that doesn't exist (yet)."""
