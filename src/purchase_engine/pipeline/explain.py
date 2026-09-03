"""Explanation generator.

For every recommendation, a plain-language "why" - positives and risks always
shown together (human-in-the-loop). Every line traces to one feature; nothing is
asserted that isn't in :class:`ProductFeatures`. Deterministic order so
golden-file diffs are stable.
"""

from __future__ import annotations

from purchase_engine.config import EngineConfig
from purchase_engine.domain.models import (
    ConfidenceBreakdown,
    ProductFeatures,
    QuantityPlan,
    ScoreBreakdown,
)

_MAX_LINES = 5


class ExplanationGenerator:
    def __init__(self, cfg: EngineConfig) -> None:
        self.cfg = cfg

    def generate(
        self,
        f: ProductFeatures,
        score: ScoreBreakdown,
        conf: ConfidenceBreakdown,
        qty: QuantityPlan,
    ) -> tuple[list[str], list[str]]:
        pos: list[str] = []
        risk: list[str] = []

        self._demand_lines(f, score, pos, risk)
        self._stock_lines(f, score, qty, pos, risk)
        self._incoming_lines(f, pos, risk)
        self._profit_lines(f, risk, pos)
        self._mapping_lines(f, conf, score, risk)

        # market note is always last, always a positive, never a penalty
        pos.append("No Keepa / Back Market rank - market weight redistributed, not penalised.")
        return pos[:_MAX_LINES], risk[:_MAX_LINES]

    # -- sections ---------------------------------------------------- #
    def _demand_lines(
        self, f: ProductFeatures, score: ScoreBreakdown, pos: list[str], risk: list[str]
    ) -> None:
        if f.velocity_window_days == 30 and f.units_30d >= self.cfg.velocity.fast_switch_units:
            if score.demand is not None:
                pos.append(
                    f"Sold {f.units_30d:.0f} units in the last 30 days "
                    f"(category percentile {score.demand:.0f})."
                )
            else:
                pos.append(f"Sold {f.units_30d:.0f} units in the last 30 days.")
        elif f.units_90d > 0:
            pos.append(
                f"Sold {f.units_90d:.0f} units in the last 90 days "
                f"(90-day rate, below the 30-day threshold)."
            )
        else:
            risk.append(
                "No sales in the last 90 days - excluded from BUY, insufficient sales history."
            )

    def _stock_lines(
        self,
        f: ProductFeatures,
        score: ScoreBreakdown,
        qty: QuantityPlan,
        pos: list[str],
        risk: list[str],
    ) -> None:
        if f.availability == "OUT_OF_STOCK":
            pos.append("JTL Auf Lager = 0 - nothing physically in the warehouse.")
        elif f.availability == "RESERVED":
            pos.append(
                f"JTL has {f.on_hand:.0f} on hand but all reserved in open orders "
                f"(Verfügbar = 0) - still a buy-need, not an empty warehouse."
            )
        elif f.availability == "LOW_STOCK":
            pos.append(
                f"JTL Verfügbar = {f.current_sellable:.0f} "
                f"(Auf Lager {f.on_hand:.0f}, in orders {f.in_orders:.0f})."
            )
        elif f.availability == "AVAILABLE":
            pos.append(f"{f.current_sellable:.0f} sellable / {f.on_hand:.0f} on hand in JTL.")
        elif f.availability == "UNAVAILABLE_STOCK":
            risk.append(
                "This Produkt-ID has no inventory row - stock treated as unknown, not "
                "zero; inventory-need redistributed."
            )

        if f.days_of_supply is not None and score.inventory_need is not None:
            if score.inventory_need >= 80:
                pos.append(
                    f"~{f.days_of_supply:.1f} days of cover vs the "
                    f"{qty.target_coverage_days:.0f}-day target - restock."
                )
            elif score.inventory_need <= 30:
                risk.append(
                    f"Effective stock already covers ~{f.days_of_supply:.1f} days "
                    f"(> {qty.target_coverage_days:.0f}-day target) - quantity is a top-up."
                )

    def _incoming_lines(self, f: ProductFeatures, pos: list[str], risk: list[str]) -> None:
        if f.purchased_today > 0:
            pos.append(
                f"{f.purchased_today} unit(s) already purchased today - counted in "
                f"Effective Stock, not recommended again."
            )
        if f.older_incoming > 0:
            risk.append(
                f"{f.older_incoming} unit(s) purchased in the last "
                f"{self.cfg.incoming.older_incoming_window[1]} days - already in Effective "
                f"Stock (rolling-window proxy, not a reconciled ledger)."
            )

    def _profit_lines(self, f: ProductFeatures, risk: list[str], pos: list[str]) -> None:
        p = f.profitability
        if p.margin_pct is not None and p.margin_pct >= 20:
            tag = {"CONFIRMED": "Confirmed", "TEMP_CALCULATED": "Provisional"}.get(
                p.status, "Estimated"
            )
            extra = (
                f", historical success {f.hist_success * 100:.0f}%."
                if f.hist_success is not None
                else "."
            )
            pos.append(f"{tag} trailing margin ~{p.margin_pct:.0f}%{extra}")
        elif p.margin_pct is not None and p.margin_pct < 0:
            risk.append(f"Trailing margin is negative ({p.margin_pct:.0f}%) - check the mapping.")
        if p.status == "UNAVAILABLE":
            risk.append(
                "No confirmed profit rows - profit component redistributed, flagged UNAVAILABLE."
            )

    def _mapping_lines(
        self,
        f: ProductFeatures,
        conf: ConfidenceBreakdown,
        score: ScoreBreakdown,
        risk: list[str],
    ) -> None:
        if f.is_duplicate:
            risk.append(
                "Active/active duplicate in Produktstamm - mapping confidence capped at 40."
            )
        elif conf.mapping <= 70:
            risk.append(
                "Resolved by the category+model fallback, not an alias - mapping confidence "
                f"capped at {conf.mapping:.0f}; main lever on why confidence isn't higher."
            )
        if score.single_component_capped:
            risk.append(
                "Score rests on a single surviving component - capped at "
                f"{self.cfg.score.single_component_cap:.0f} rather than shown as a bare 100."
            )
