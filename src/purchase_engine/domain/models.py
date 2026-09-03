"""Typed value objects passed between the engine's stages.

Plain dataclasses on purpose - no pydantic, no ORM. :func:`to_jsonable` turns any
of them into JSON-safe primitives for the append-only recommendation store and
the (Phase 3) API.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

# The only profitability vocabulary the scoring / confidence code is allowed to
# see (Technical Implementation Plan - Profit Engine interface).
PROFIT_STATUS: tuple[str, ...] = ("CONFIRMED", "TEMP_CALCULATED", "PRÜFEN", "UNAVAILABLE")

# Derived stock-availability badge. UNAVAILABLE_STOCK == "no inventory row at
# all" - never treated as zero stock.
AVAILABILITY: tuple[str, ...] = (
    "AVAILABLE",
    "LOW_STOCK",
    "RESERVED",
    "OUT_OF_STOCK",
    "UNAVAILABLE_STOCK",
)

LABELS: tuple[str, ...] = ("BUY", "CONSIDER", "SKIP")


# --------------------------------------------------------------------------- #
# Raw parser input (produced by adapters.workbook, consumed by the pipeline)   #
# --------------------------------------------------------------------------- #
@dataclass
class ParserTables:
    """Cleaned, canonically-named copies of the parser output the engine needs."""

    produktstamm: pd.DataFrame
    tagesprofite: pd.DataFrame
    inventar_bestand: pd.DataFrame
    inventar_mapping: pd.DataFrame
    ek_normalisiert: pd.DataFrame
    zusammenfuehrung: pd.DataFrame
    ek_regeln: pd.DataFrame
    workbook_updated: datetime | None


# --------------------------------------------------------------------------- #
# Profitability seam                                                          #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProductProfitability:
    """The fixed 6-field shape the scoring code sees. The real Profit Engine
    swaps in behind this with no scoring change - only ``source`` changes."""

    produkt_id: str
    expected_vk: float | None
    expected_ek: float | None
    expected_gross_profit: float | None
    margin_pct: float | None
    status: str  # one of PROFIT_STATUS
    source: str  # "trailing_window" -> "profit_engine_v1"

    def __post_init__(self) -> None:
        if self.status not in PROFIT_STATUS:
            msg = f"bad profitability status: {self.status!r}"
            raise ValueError(msg)


# --------------------------------------------------------------------------- #
# Features                                                                    #
# --------------------------------------------------------------------------- #
@dataclass
class ProductFeatures:
    produkt_id: str
    name: str
    kategorie: str
    modell: str
    is_duplicate: bool

    # demand
    units_30d: float
    units_90d: float
    daily_velocity: float | None  # None => insufficient sales history
    velocity_window_days: int  # 30 | 90 | 0
    days_since_sale: int | None

    # stock position
    inventory_joined: bool
    current_sellable: float | None  # JTL Verfügbar (None => no inventory row)
    on_hand: float | None  # JTL Auf Lager
    in_orders: float | None  # JTL In Aufträgen
    purchased_today: int
    older_incoming: int
    effective_stock: float
    days_of_supply: float | None
    availability: str
    join_source: str
    mapping_quelle: str

    # profitability (already resolved through the seam)
    profitability: ProductProfitability
    margin_pct: float | None
    hist_success: float | None
    ok_rows: int


# --------------------------------------------------------------------------- #
# Scores                                                                      #
# --------------------------------------------------------------------------- #
@dataclass
class ScoreBreakdown:
    demand: float | None
    inventory_need: float | None
    profit: float | None
    market: float | None  # None => UNAVAILABLE, redistributed
    overstock_penalty: float
    effective_weights: dict[str, float]  # component -> weight actually applied
    single_component_capped: bool
    score: int  # 0..100


@dataclass
class ConfidenceBreakdown:
    mapping: float
    sales_sufficiency: float
    inventory_reliability: float
    profitability_reliability: float
    confidence: int  # 0..100


@dataclass
class QuantityPlan:
    daily_velocity: float | None
    target_coverage_days: float
    effective_stock: float
    required_units: int | None  # ceil(v*T - stock), pre-floor/cap
    per_sku_capped_qty: int  # after max(0, .) and the per-SKU exposure cap
    recommended_qty: int  # after the daily-budget allocation pass
    per_sku_cap: int
    budget_trimmed: bool


# --------------------------------------------------------------------------- #
# Recommendation                                                              #
# --------------------------------------------------------------------------- #
@dataclass
class Recommendation:
    produkt_id: str
    name: str
    kategorie: str
    modell: str
    label: str
    purchase_score: int
    confidence: int
    recommended_qty: int
    availability: str

    features: ProductFeatures
    score: ScoreBreakdown
    confidence_breakdown: ConfidenceBreakdown
    quantity: QuantityPlan

    reasons: list[str]
    risks: list[str]

    # budget-allocation economics (None when profitability is UNAVAILABLE)
    est_unit_ek: float | None = None
    est_gross_profit_per_eur: float | None = None
    est_total_cost: float | None = None
    est_total_gross_profit: float | None = None


@dataclass
class DataFreshness:
    as_of: str
    sales_through: str | None
    purchases_through: str | None
    workbook_updated: str | None
    run_calendar_date: str
    stale: bool
    note: str = ""


@dataclass
class RecommendationSet:
    run_id: str
    generated_at: str
    as_of: str
    budget_eur: float | None
    config_hash: str
    data_freshness: DataFreshness
    counts: dict[str, int]
    recommendations: list[Recommendation] = field(default_factory=list)

    def by_label(self, label: str) -> list[Recommendation]:
        return [r for r in self.recommendations if r.label == label]


# --------------------------------------------------------------------------- #
# Serialisation                                                               #
# --------------------------------------------------------------------------- #
def to_jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses / dates / NaN into JSON-safe values."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, float):
        return None if math.isnan(obj) else round(obj, 4)
    return obj
