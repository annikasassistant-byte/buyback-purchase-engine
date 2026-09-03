"""Purchase Score (0-100).

::

    PurchaseScore =
        W_demand    * DemandScore          (35)
      + W_inventory * InventoryNeedScore   (30)
      + W_profit    * ProfitScore          (25)
      + W_market    * MarketScore          (10, redistributed while UNAVAILABLE)
      - OverstockSlowMoverPenalty          (0..-20)

Missing components are redistributed proportionally over the survivors, never
zero-filled - a product is never capped just because an optional signal isn't
built. That uncertainty shows up in the Confidence Score instead. The one guard:
a score resting on a *single* surviving component is capped
(``score.single_component_cap``), because a bare "100" off one axis is
misleading (the plan's "Canon 18-55mm" case).
"""

from __future__ import annotations

import pandas as pd

from purchase_engine.config import EngineConfig
from purchase_engine.domain.models import ProductFeatures, ScoreBreakdown


def _percentile_within_category(df: pd.DataFrame, value_col: str) -> pd.Series:
    """0-100 percentile of ``value_col`` within each ``kategorie``. A category
    with <=1 usable value -> 50 (no information to rank on)."""

    def _rank(s: pd.Series) -> pd.Series:
        usable = s.dropna()
        if usable.nunique() <= 1:
            return pd.Series(50.0, index=s.index).where(s.notna())
        return s.rank(method="average", pct=True) * 100.0

    return df.groupby("kategorie")[value_col].transform(_rank)


class PurchaseScorer:
    def __init__(self, cfg: EngineConfig) -> None:
        self.cfg = cfg

    def _overstock_penalty(self, f: ProductFeatures) -> float:
        o = self.cfg.score
        target = self.cfg.quantity.target_coverage_days
        if not f.inventory_joined or f.days_of_supply is None or f.ok_rows == 0:
            return 0.0
        if f.days_since_sale is None or f.days_since_sale < o.overstock_min_days_since_sale:
            return 0.0
        limit = o.overstock_supply_multiple * target
        if f.days_of_supply <= limit:
            return 0.0
        pen = (f.days_of_supply / limit - 1.0) * 10.0
        return float(min(o.overstock_max_points, pen))

    def score_all(self, features: list[ProductFeatures]) -> dict[str, ScoreBreakdown]:
        cfg = self.cfg
        rows = pd.DataFrame(
            [
                {
                    "produkt_id": f.produkt_id,
                    "kategorie": f.kategorie,
                    "velocity": f.daily_velocity if f.velocity_window_days > 0 else None,
                    "margin_pct": f.margin_pct if f.ok_rows > 0 else None,
                }
                for f in features
            ]
        )
        rows["demand_pct"] = _percentile_within_category(rows, "velocity")
        rows["margin_pct_rank"] = _percentile_within_category(rows, "margin_pct")
        demand_by_pid = dict(zip(rows["produkt_id"], rows["demand_pct"], strict=True))
        margin_rank_by_pid = dict(zip(rows["produkt_id"], rows["margin_pct_rank"], strict=True))

        target = cfg.quantity.target_coverage_days
        weights = cfg.score.weights
        out: dict[str, ScoreBreakdown] = {}
        for f in features:
            demand = demand_by_pid.get(f.produkt_id)
            demand = None if pd.isna(demand) else float(demand)

            inv_need: float | None = None
            if f.inventory_joined and f.velocity_window_days > 0:
                if (f.current_sellable or 0) == 0:
                    # Nothing sellable *today* = maximum restock urgency, even if
                    # units are incoming (they haven't arrived). Incoming crushes
                    # the *quantity*, not this score. Plan's Galaxy A54 case.
                    inv_need = 100.0
                elif f.days_of_supply is not None:
                    # Sellable stock exists -> days-of-supply gap on EFFECTIVE
                    # stock (incl. incoming). Plan's Xbox One S case.
                    inv_need = max(
                        0.0, min(100.0, (1.0 - min(f.days_of_supply, target) / target) * 100.0)
                    )

            profit: float | None = None
            if f.ok_rows > 0:
                mr = margin_rank_by_pid.get(f.produkt_id)
                mr = 50.0 if pd.isna(mr) else float(mr)
                hs = (f.hist_success if f.hist_success is not None else 0.0) * 100.0
                profit = (
                    mr * cfg.profit_score_margin_weight + hs * cfg.profit_score_hist_success_weight
                )

            market: float | None = None  # MVP: Keepa / Back Market not integrated

            penalty = self._overstock_penalty(f)

            present = {
                "demand": demand,
                "inventory_need": inv_need,
                "profit": profit,
                "market": market,
            }
            live = {k: v for k, v in present.items() if v is not None}
            eff_weights: dict[str, float] = {}
            if live:
                tw = sum(weights[k] for k in live)
                eff_weights = {k: (weights[k] / tw) for k in live} if tw > 0 else {}
                raw = sum(eff_weights[k] * live[k] for k in live)
            else:
                raw = 0.0

            capped = False
            score = raw - penalty
            if (
                len(live) == 1
                and cfg.score.single_component_cap is not None
                and score > cfg.score.single_component_cap
            ):
                score = cfg.score.single_component_cap
                capped = True
            score = max(0.0, min(100.0, score))

            out[f.produkt_id] = ScoreBreakdown(
                demand=demand,
                inventory_need=inv_need,
                profit=profit,
                market=market,
                overstock_penalty=round(penalty, 2),
                effective_weights={k: round(v, 4) for k, v in eff_weights.items()},
                single_component_capped=capped,
                score=round(score),
            )
        return out
