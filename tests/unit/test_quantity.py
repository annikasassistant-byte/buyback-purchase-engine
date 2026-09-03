from __future__ import annotations

import math

from purchase_engine.domain.models import ProductProfitability
from purchase_engine.pipeline.quantity import BudgetAllocator, QuantityPlanner
from tests._factories import mkfeat


def test_order_up_to_level_formula(cfg):
    t = cfg.quantity.target_coverage_days
    f = mkfeat("BB1", vel=1.0, older_incoming=3, sellable=0.0)  # effective_stock 3
    plan = QuantityPlanner(cfg).plan(f)
    assert plan.required_units == math.ceil(1.0 * t - 3)
    assert plan.recommended_qty == min(plan.required_units, cfg.quantity.per_sku_max_exposure)


def test_no_velocity_means_zero_quantity(cfg):
    plan = QuantityPlanner(cfg).plan(mkfeat("BB1", vel=None, win=0, u30=0, u90=0))
    assert plan.required_units is None
    assert plan.recommended_qty == 0


def test_incoming_stock_reduces_quantity(cfg):
    qa = (
        QuantityPlanner(cfg)
        .plan(mkfeat("A", vel=0.4, older_incoming=0, sellable=0.0))
        .recommended_qty
    )
    qb = (
        QuantityPlanner(cfg)
        .plan(mkfeat("B", vel=0.4, older_incoming=5, sellable=0.0))
        .recommended_qty
    )
    assert qb < qa


def test_per_sku_cap_applies(cfg):
    plan = QuantityPlanner(cfg).plan(mkfeat("BB1", vel=10.0, older_incoming=0, sellable=0.0))
    assert plan.recommended_qty == cfg.quantity.per_sku_max_exposure


def test_budget_allocation_ranks_by_gp_per_euro_and_trims_marginal(cfg):
    planner = QuantityPlanner(cfg)
    feats = {
        "RICH": mkfeat("RICH", vel=1.0, sellable=0.0),
        "POOR": mkfeat("POOR", vel=1.0, sellable=0.0),
    }
    plans = {pid: planner.plan(f) for pid, f in feats.items()}
    prof = {
        "RICH": ProductProfitability("RICH", 200, 50, 150, 75, "CONFIRMED", "x"),  # GP/€ 3.0
        "POOR": ProductProfitability("POOR", 120, 100, 20, 16, "CONFIRMED", "x"),  # GP/€ 0.2
    }
    lines = BudgetAllocator(cfg).allocate(plans, feats, prof, budget_eur=450.0)
    assert lines["RICH"].final_qty == plans["RICH"].recommended_qty
    assert lines["POOR"].final_qty < plans["POOR"].recommended_qty
    assert lines["POOR"].trimmed is True


def test_no_budget_means_no_trimming(cfg):
    planner = QuantityPlanner(cfg)
    f = mkfeat("BB1", vel=1.0, sellable=0.0)
    plans = {"BB1": planner.plan(f)}
    prof = {"BB1": ProductProfitability("BB1", 200, 50, 150, 75, "CONFIRMED", "x")}
    lines = BudgetAllocator(cfg).allocate(plans, {"BB1": f}, prof, budget_eur=None)
    assert lines["BB1"].final_qty == plans["BB1"].recommended_qty
    assert lines["BB1"].trimmed is False
