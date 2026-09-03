"""Adapters - concrete implementations of the domain ports and the reader for
the parser workbook. This is the only layer that knows about Excel, the file
system, or SQLite.
"""

from __future__ import annotations

from purchase_engine.adapters.incoming import (
    EkNormalisiertIncoming,
    LivePurchaseTableIncoming,
    NullIncoming,
    build_incoming_source,
)
from purchase_engine.adapters.profitability import StaticProfitability, TrailingWindowProfitability
from purchase_engine.adapters.store import FileStore, MultiStore, NullStore, SqliteStore
from purchase_engine.adapters.workbook import ParserWorkbook

__all__ = [
    "EkNormalisiertIncoming",
    "FileStore",
    "LivePurchaseTableIncoming",
    "MultiStore",
    "NullIncoming",
    "NullStore",
    "ParserWorkbook",
    "SqliteStore",
    "StaticProfitability",
    "TrailingWindowProfitability",
    "build_incoming_source",
]
