"""Ports - the contracts the pipeline depends on and the adapters implement.

Hexagonal boundary: ``pipeline`` code type-hints against these Protocols and is
handed a concrete adapter at construction time. Adding a new data source or
storage target means writing an adapter that satisfies one of these - the
pipeline does not change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from purchase_engine.domain.models import ProductProfitability, RecommendationSet


# --------------------------------------------------------------------------- #
# Incoming stock                                                              #
# --------------------------------------------------------------------------- #
@dataclass
class IncomingCounts:
    """Per-model-key unit counts, keyed by UPPER-cased Modellschlüssel."""

    purchased_today: dict[str, int] = field(default_factory=dict)
    older_incoming: dict[str, int] = field(default_factory=dict)
    # provenance for the run log / explanations
    source: str = ""
    rows_considered: int = 0
    rows_today: int = 0
    rows_window: int = 0
    rows_rejected_bad_date: int = 0

    def for_model(self, model_key: str) -> tuple[int, int]:
        mk = (model_key or "").upper()
        return self.purchased_today.get(mk, 0), self.older_incoming.get(mk, 0)


@runtime_checkable
class IncomingStockSource(Protocol):
    """Units already bought but not yet sellable, added to Effective Stock."""

    def counts(self, as_of: datetime) -> IncomingCounts: ...


# --------------------------------------------------------------------------- #
# Profitability                                                               #
# --------------------------------------------------------------------------- #
@runtime_checkable
class Profitability(Protocol):
    """Resolves a product to the fixed 6-field :class:`ProductProfitability`."""

    def get_profitability(self, produkt_id: str, as_of: datetime) -> ProductProfitability: ...


# --------------------------------------------------------------------------- #
# Recommendation history                                                      #
# --------------------------------------------------------------------------- #
@runtime_checkable
class RecommendationStore(Protocol):
    """Append-only sink for every run's recommendation set."""

    def save(self, result: RecommendationSet) -> None: ...
