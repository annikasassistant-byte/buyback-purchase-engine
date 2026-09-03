"""Recommended Quantity + daily-budget allocation.

Quantity is an order-up-to-level (base-stock) calculation - the periodic-review
counterpart of the reorder-point formula, correct here because recommendations
run on a schedule rather than off live stock events. Computed in parallel with
the Purchase Score from the same features, never as a multiplier of it.

::

    EffectiveStockPosition = CurrentSellable(Verfügbar) + PurchasedToday + OlderIncoming
    DailyVelocity          = units(30d)/30 if units(30d) >= 3 else units(90d)/90 else UNAVAILABLE
    RequiredUnits          = ceil(DailyVelocity * TargetCoverageDays - EffectiveStockPosition)
    RecommendedQuantity    = max(0, RequiredUnits) capped at PerSkuMaxExposure,
                             then rationed by the daily-budget allocation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from purchase_engine.config import EngineConfig
from purchase_engine.domain.models import ProductFeatures, ProductProfitability, QuantityPlan


class QuantityPlanner:
    def __init__(self, cfg: EngineConfig) -> None:
        self.cfg = cfg

    def _per_sku_cap(self, kategorie: str) -> int:
        q = self.cfg.quantity
        return int(q.per_category_max_exposure.get(kategorie, q.per_sku_max_exposure))

    def plan(self, f: ProductFeatures) -> QuantityPlan:
        target = self.cfg.quantity.target_coverage_days
        cap = self._per_sku_cap(f.kategorie)
        if f.velocity_window_days == 0 or not f.daily_velocity:
            return QuantityPlan(
                daily_velocity=f.daily_velocity,
                target_coverage_days=target,
                effective_stock=f.effective_stock,
                required_units=None,
                per_sku_capped_qty=0,
                recommended_qty=0,
                per_sku_cap=cap,
                budget_trimmed=False,
            )
        required = math.ceil(f.daily_velocity * target - f.effective_stock)
        capped = max(0, min(int(required), cap))
        return QuantityPlan(
            daily_velocity=f.daily_velocity,
            target_coverage_days=target,
            effective_stock=f.effective_stock,
            required_units=int(required),
            per_sku_capped_qty=capped,
            recommended_qty=capped,
            per_sku_cap=cap,
            budget_trimmed=False,
        )

    def plan_all(self, features: list[ProductFeatures]) -> dict[str, QuantityPlan]:
        return {f.produkt_id: self.plan(f) for f in features}


@dataclass
class AllocationLine:
    produkt_id: str
    final_qty: int
    trimmed: bool
    unit_ek: float | None
    gp_per_eur: float | None
    total_cost: float | None
    total_gross_profit: float | None


class BudgetAllocator:
    """Greedy: rank the BUY list by expected gross profit per euro of purchase
    price, fill the daily budget top-down, trim the marginal product. Ranks on
    GP/EUR only - a low-confidence line keeps its budget and stays visible; the
    buyer UI shows the flag, the allocator does not hide it (plan, "Quantity").
    """

    def __init__(self, cfg: EngineConfig) -> None:
        self.cfg = cfg

    def allocate(
        self,
        buy_plans: dict[str, QuantityPlan],
        features: dict[str, ProductFeatures],
        profitability: dict[str, ProductProfitability],
        budget_eur: float | None,
    ) -> dict[str, AllocationLine]:
        lines: dict[str, AllocationLine] = {}
        ranked: list[tuple[float, str]] = []
        for pid, plan in buy_plans.items():
            prof = profitability.get(pid)
            unit_ek = prof.expected_ek if prof else None
            if unit_ek is not None and (not math.isfinite(unit_ek) or unit_ek <= 0):
                unit_ek = None
            gp_per_eur: float | None = None
            if prof and prof.expected_gross_profit is not None and unit_ek:
                gp_per_eur = prof.expected_gross_profit / unit_ek
            lines[pid] = AllocationLine(
                produkt_id=pid,
                final_qty=plan.recommended_qty,
                trimmed=False,
                unit_ek=unit_ek,
                gp_per_eur=gp_per_eur,
                total_cost=(unit_ek * plan.recommended_qty) if unit_ek else None,
                total_gross_profit=(
                    prof.expected_gross_profit * plan.recommended_qty
                    if prof and prof.expected_gross_profit is not None
                    else None
                ),
            )
            ranked.append((gp_per_eur if gp_per_eur is not None else -1.0, pid))

        if budget_eur is None:
            return lines

        ranked.sort(key=lambda t: t[0], reverse=True)
        remaining = float(budget_eur)
        for _, pid in ranked:
            line = lines[pid]
            plan = buy_plans[pid]
            if line.unit_ek is None or line.unit_ek <= 0:
                continue  # can't cost it - leave qty, don't touch the budget
            affordable = int(remaining // line.unit_ek)
            take = max(0, min(plan.recommended_qty, affordable))
            line.trimmed = take < plan.recommended_qty
            line.final_qty = take
            line.total_cost = take * line.unit_ek
            prof = profitability.get(pid)
            line.total_gross_profit = (
                prof.expected_gross_profit * take
                if prof and prof.expected_gross_profit is not None
                else None
            )
            remaining -= line.total_cost
        return lines
