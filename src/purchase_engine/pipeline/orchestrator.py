"""Orchestrator: parser workbook -> :class:`RecommendationSet`.

::

    features  ->  Purchase Score  +  Confidence Score  (side by side, never multiplied)
              ->  Quantity (order-up-to-level)
              ->  daily-budget allocation (greedy GP/EUR)
              ->  explanation (reasons + risks together)
              ->  append-only history

Idempotent: re-running over the same workbook + config + as_of produces the same
recommendations (only ``run_id`` / ``generated_at`` differ).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from purchase_engine._normalize import build_merge_redirect
from purchase_engine.adapters.incoming import build_incoming_source
from purchase_engine.adapters.profitability import TrailingWindowProfitability
from purchase_engine.adapters.store import NullStore
from purchase_engine.adapters.workbook import ParserWorkbook
from purchase_engine.config import EngineConfig, load_config
from purchase_engine.domain.models import (
    DataFreshness,
    ParserTables,
    Recommendation,
    RecommendationSet,
)
from purchase_engine.domain.ports import RecommendationStore
from purchase_engine.pipeline.confidence import ConfidenceScorer
from purchase_engine.pipeline.explain import ExplanationGenerator
from purchase_engine.pipeline.features import FeatureBuilder, resolve_as_of
from purchase_engine.pipeline.quantity import BudgetAllocator, QuantityPlanner
from purchase_engine.pipeline.scoring import PurchaseScorer

log = logging.getLogger(__name__)

_LABEL_ORDER = {"BUY": 0, "CONSIDER": 1, "SKIP": 2}


class Engine:
    """Wire the pipeline together and produce a persisted recommendation set."""

    def __init__(self, cfg: EngineConfig, store: RecommendationStore | None = None) -> None:
        self.cfg = cfg
        self.store: RecommendationStore = store or NullStore()

    @classmethod
    def from_config_path(
        cls, path: str | Path | None = None, store: RecommendationStore | None = None
    ) -> Engine:
        return cls(load_config(path), store)

    # -- run ------------------------------------------------------------ #
    def run(
        self,
        workbook_path: str | Path | None = None,
        as_of: datetime | None = None,
        budget_eur: float | None = None,
        *,
        tables: ParserTables | None = None,
    ) -> RecommendationSet:
        if tables is None:
            if workbook_path is None:
                msg = "run() needs either workbook_path or tables="
                raise ValueError(msg)
            tables = ParserWorkbook(workbook_path).load()

        # merge-redirect historical Produkt-IDs once, up front (SCD-lite)
        redirect = build_merge_redirect(tables.zusammenfuehrung)
        tables.tagesprofite["produkt_id"] = tables.tagesprofite["produkt_id"].map(
            lambda p: redirect.get(p, p)
        )

        run_as_of = as_of or resolve_as_of(tables, self.cfg)
        eff_budget = budget_eur if budget_eur is not None else self.cfg.budget.daily_budget_eur
        log.info("run as_of=%s budget=%s config=%s", run_as_of.date(), eff_budget, self.cfg.hash)

        active = tables.produktstamm
        active = active[
            (active["aktiv"] == "JA") & active["produkt_id"].astype(str).str.startswith("BB")
        ]
        known_model_keys = {str(m).upper() for m in active["modellschluessel"] if str(m).strip()}
        model_key_by_pid = dict(
            zip(
                active["produkt_id"].astype(str),
                active["modellschluessel"].astype(str),
                strict=True,
            )
        )

        incoming_src = build_incoming_source(tables, self.cfg.incoming, known_model_keys)
        profitability = TrailingWindowProfitability(
            tables.tagesprofite, tables.ek_normalisiert, model_key_by_pid, self.cfg.profitability
        )

        fb = FeatureBuilder(tables, self.cfg, incoming_src, profitability)
        features = fb.build(run_as_of)
        feat_by_pid = {f.produkt_id: f for f in features}

        scores = PurchaseScorer(self.cfg).score_all(features)
        confs = ConfidenceScorer(self.cfg).score_all(features)
        plans = QuantityPlanner(self.cfg).plan_all(features)

        buy_min = self.cfg.score.buy_min
        consider_min = self.cfg.score.consider_min
        labels: dict[str, str] = {}
        for f in features:
            s = scores[f.produkt_id].score
            plan = plans[f.produkt_id]
            if s >= buy_min:
                lab = "BUY"
            elif s >= consider_min:
                lab = "CONSIDER"
            else:
                lab = "SKIP"
            if lab == "BUY" and (f.velocity_window_days == 0 or plan.per_sku_capped_qty == 0):
                lab = "CONSIDER"  # scored well on one axis but nothing to actually buy
            labels[f.produkt_id] = lab

        buy_plans = {
            pid: plans[pid]
            for pid, lab in labels.items()
            if lab == "BUY" and plans[pid].per_sku_capped_qty > 0
        }
        prof_by_pid = {f.produkt_id: f.profitability for f in features}
        alloc = BudgetAllocator(self.cfg).allocate(buy_plans, feat_by_pid, prof_by_pid, eff_budget)
        for pid, line in alloc.items():
            plans[pid].recommended_qty = line.final_qty
            plans[pid].budget_trimmed = line.trimmed

        explainer = ExplanationGenerator(self.cfg)
        recs: list[Recommendation] = []
        for f in features:
            sb = scores[f.produkt_id]
            cb = confs[f.produkt_id]
            plan = plans[f.produkt_id]
            reasons, risks = explainer.generate(f, sb, cb, plan)
            if plan.budget_trimmed:
                msg = (
                    f"Daily budget (€{eff_budget:,.0f}) reached before this line: "
                    f"{plan.per_sku_capped_qty} unit(s) justified by velocity, "
                    f"{plan.recommended_qty} funded today - still a valid BUY, just not "
                    f"in today's budget."
                )
                risks = ([msg, *risks])[:5]
            econ = alloc.get(f.produkt_id)
            recs.append(
                Recommendation(
                    produkt_id=f.produkt_id,
                    name=f.name,
                    kategorie=f.kategorie,
                    modell=f.modell,
                    label=labels[f.produkt_id],
                    purchase_score=sb.score,
                    confidence=cb.confidence,
                    recommended_qty=plan.recommended_qty,
                    availability=f.availability,
                    features=f,
                    score=sb,
                    confidence_breakdown=cb,
                    quantity=plan,
                    reasons=reasons,
                    risks=risks,
                    est_unit_ek=(econ.unit_ek if econ else f.profitability.expected_ek),
                    est_gross_profit_per_eur=(econ.gp_per_eur if econ else None),
                    est_total_cost=(econ.total_cost if econ else None),
                    est_total_gross_profit=(econ.total_gross_profit if econ else None),
                )
            )

        # BUY tier leads with today's actual buy list: funded first, ordered by
        # the same GP/EUR the budget allocator ranks on, then by score. Golden
        # test re-sorts by produkt_id, so this ordering is consumer sugar.
        recs.sort(
            key=lambda r: (
                _LABEL_ORDER[r.label],
                0 if (r.label == "BUY" and r.recommended_qty > 0) else 1,
                -((r.est_gross_profit_per_eur or -1.0) if r.label == "BUY" else 0.0),
                -r.purchase_score,
                -(r.features.daily_velocity or 0.0),
                r.produkt_id,
            )
        )

        counts = {
            "scored": len(recs),
            "buy": sum(r.label == "BUY" for r in recs),
            "buy_funded": sum(r.label == "BUY" and r.recommended_qty > 0 for r in recs),
            "consider": sum(r.label == "CONSIDER" for r in recs),
            "skip": sum(r.label == "SKIP" for r in recs),
            "buy_actionable": sum(
                r.label == "BUY"
                and r.features.velocity_window_days == 30
                and r.features.units_30d >= self.cfg.velocity.fast_switch_units
                for r in recs
            ),
            "inventory_joined": sum(r.features.inventory_joined for r in recs),
            "incoming_rows_today": getattr(fb.last_incoming, "rows_today", 0),
            "incoming_rows_window": getattr(fb.last_incoming, "rows_window", 0),
        }

        result = RecommendationSet(
            run_id=f"{run_as_of:%Y%m%d}-{datetime.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}",
            generated_at=datetime.now().isoformat(timespec="seconds"),
            as_of=run_as_of.date().isoformat(),
            budget_eur=eff_budget,
            config_hash=self.cfg.hash,
            data_freshness=self._freshness(tables, run_as_of),
            counts=counts,
            recommendations=recs,
        )
        self.store.save(result)
        log.info(
            "done: BUY %d (%d funded) / CONSIDER %d / SKIP %d",
            counts["buy"],
            counts["buy_funded"],
            counts["consider"],
            counts["skip"],
        )
        return result

    # -- freshness ------------------------------------------------------ #
    def _freshness(self, tables: ParserTables, as_of: datetime) -> DataFreshness:
        sales = pd.to_datetime(tables.tagesprofite["auftragsdatum"], errors="coerce").dropna()
        kd = pd.to_datetime(tables.ek_normalisiert["kaufdatum"], errors="coerce")
        kd = kd[
            (kd >= pd.Timestamp("2024-01-01"))
            & (kd <= pd.Timestamp(datetime.now()) + pd.Timedelta(days=2))
        ]
        sales_through = sales.max().date().isoformat() if len(sales) else None
        purchases_through = kd.max().date().isoformat() if len(kd.dropna()) else None
        today = date.today()
        stale = False
        note = ""
        if sales_through:
            lag = (today - date.fromisoformat(sales_through)).days
            if lag > self.cfg.data.stale_after_days:
                stale = True
                note = (
                    f"STALE_INPUTS: newest sale {sales_through} is {lag} days old "
                    f"(threshold {self.cfg.data.stale_after_days}). List still produced."
                )
        return DataFreshness(
            as_of=as_of.date().isoformat(),
            sales_through=sales_through,
            purchases_through=purchases_through,
            workbook_updated=(
                tables.workbook_updated.isoformat() if tables.workbook_updated else None
            ),
            run_calendar_date=today.isoformat(),
            stale=stale,
            note=note,
        )
