"""Pipeline - the pure computation: features -> scores -> quantity -> explanation.

Depends only on ``domain`` and ``config``. Concrete data sources and storage are
injected as ports, so this layer is fully unit-testable without a workbook.
"""

from __future__ import annotations

from purchase_engine.pipeline.confidence import ConfidenceScorer
from purchase_engine.pipeline.explain import ExplanationGenerator
from purchase_engine.pipeline.features import FeatureBuilder, resolve_as_of
from purchase_engine.pipeline.orchestrator import Engine
from purchase_engine.pipeline.quantity import BudgetAllocator, QuantityPlanner
from purchase_engine.pipeline.scoring import PurchaseScorer

__all__ = [
    "BudgetAllocator",
    "ConfidenceScorer",
    "Engine",
    "ExplanationGenerator",
    "FeatureBuilder",
    "PurchaseScorer",
    "QuantityPlanner",
    "resolve_as_of",
]
