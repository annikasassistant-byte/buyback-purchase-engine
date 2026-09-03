"""BuyBack Purchase Engine - Phase 2 MVP.

Reads the existing parser's output (the "BuyBack - Profit" workbook) read-only,
then computes, per active product:

* a 0-100 **Purchase Score** (weighted, config-driven, missing parts redistributed),
* an independent 0-100 **Confidence Score** (never multiplied with the score),
* an order-up-to-level **Quantity** with a greedy daily-budget allocation, and
* a plain-language **explanation** (reasons + risks, always together).

Every run is appended to an on-disk history - the dataset for the forward-looking
backtest.

Layering (imports point inward only)::

    domain  <-  adapters  <-  pipeline  <-  cli

See ``docs/architecture.md``.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.2.0"
