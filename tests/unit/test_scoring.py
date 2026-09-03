from __future__ import annotations

import pytest

from purchase_engine.pipeline.scoring import PurchaseScorer
from tests._factories import mkfeat


def test_market_component_is_redistributed_not_zero_filled(cfg):
    sb = PurchaseScorer(cfg).score_all([mkfeat("BB1")])["BB1"]
    assert sb.market is None
    assert set(sb.effective_weights) == {"demand", "inventory_need", "profit"}
    assert sum(sb.effective_weights.values()) == pytest.approx(1.0, abs=1e-6)
    assert 0 <= sb.score <= 100


def test_single_component_score_is_capped(cfg):
    hi = mkfeat("HI", vel=None, win=0, u30=0, u90=0, dss=None, margin=80.0, ok_rows=9, hist=1.0)
    lo = mkfeat("LO", vel=None, win=0, u30=0, u90=0, dss=None, margin=5.0, ok_rows=9, hist=1.0)
    sb = PurchaseScorer(cfg).score_all([hi, lo])["HI"]
    assert sb.demand is None and sb.inventory_need is None
    assert sb.profit == pytest.approx(100.0)
    assert sb.single_component_capped is True
    assert sb.score <= cfg.score.single_component_cap


def test_overstock_penalty_only_on_genuine_slow_movers(cfg):
    t = cfg.quantity.target_coverage_days
    out = PurchaseScorer(cfg).score_all(
        [mkfeat("HOT", dos=5.0, dss=3), mkfeat("COLD", dos=6 * t, dss=120)]
    )
    assert out["HOT"].overstock_penalty == 0.0
    assert out["COLD"].overstock_penalty > 0.0


def test_score_is_monotonic_in_demand(cfg):
    out = PurchaseScorer(cfg).score_all(
        [mkfeat("LO", vel=0.10, u30=3.0), mkfeat("HI", vel=5.00, u30=150.0)]
    )
    hi, lo = out["HI"].demand, out["LO"].demand
    assert hi is not None and lo is not None and hi > lo
    assert out["HI"].score >= out["LO"].score


def test_missing_inventory_row_drops_inventory_need(cfg):
    sb = PurchaseScorer(cfg).score_all(
        [mkfeat("BB1", joined=False, sellable=None, avail="UNAVAILABLE_STOCK")]
    )["BB1"]
    assert sb.inventory_need is None
    assert "inventory_need" not in sb.effective_weights


def test_verfuegbar_zero_forces_full_inventory_need(cfg):
    # plan's Galaxy A54 case: nothing sellable -> 100 even with incoming
    sb = PurchaseScorer(cfg).score_all([mkfeat("BB1", sellable=0.0, older_incoming=8, dos=6.0)])[
        "BB1"
    ]
    assert sb.inventory_need == 100.0
