"""Domain layer - pure value objects and the ports the outer layers implement.

No dependency on adapters, the pipeline, or the CLI. Everything here is either a
dataclass or a ``typing.Protocol``.
"""

from __future__ import annotations

from purchase_engine.domain.models import (
    AVAILABILITY,
    LABELS,
    PROFIT_STATUS,
    ConfidenceBreakdown,
    DataFreshness,
    ParserTables,
    ProductFeatures,
    ProductProfitability,
    QuantityPlan,
    Recommendation,
    RecommendationSet,
    ScoreBreakdown,
    to_jsonable,
)
from purchase_engine.domain.ports import (
    IncomingCounts,
    IncomingStockSource,
    Profitability,
    RecommendationStore,
)

__all__ = [
    "AVAILABILITY",
    "LABELS",
    "PROFIT_STATUS",
    "ConfidenceBreakdown",
    "DataFreshness",
    "IncomingCounts",
    "IncomingStockSource",
    "ParserTables",
    "ProductFeatures",
    "ProductProfitability",
    "Profitability",
    "QuantityPlan",
    "Recommendation",
    "RecommendationSet",
    "RecommendationStore",
    "ScoreBreakdown",
    "to_jsonable",
]
