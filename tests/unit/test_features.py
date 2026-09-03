from __future__ import annotations

from datetime import timedelta

import pytest

from purchase_engine._normalize import build_merge_redirect
from purchase_engine.adapters.incoming import EkNormalisiertIncoming
from purchase_engine.adapters.profitability import TrailingWindowProfitability
from purchase_engine.pipeline.features import FeatureBuilder
from tests._factories import AS_OF, sales_rows


def _features(tables, cfg):
    active = tables.produktstamm
    active = active[active["aktiv"] == "JA"]
    mk_by_pid = dict(zip(active["produkt_id"], active["modellschluessel"], strict=True))
    known = {m.upper() for m in mk_by_pid.values()}
    incoming = EkNormalisiertIncoming(tables.ek_normalisiert, cfg.incoming, known)
    prof = TrailingWindowProfitability(
        tables.tagesprofite, tables.ek_normalisiert, mk_by_pid, cfg.profitability
    )
    return {f.produkt_id: f for f in FeatureBuilder(tables, cfg, incoming, prof).build(AS_OF)}


def test_only_active_bb_products_are_scored(tables, cfg):
    assert set(_features(tables, cfg)) == {"BB000001", "BB000002", "BB000003", "BB000004"}


def test_velocity_window_switch(tables, cfg):
    feats = _features(tables, cfg)
    assert feats["BB000001"].velocity_window_days == 30
    assert feats["BB000001"].daily_velocity == pytest.approx(16 / 30, rel=1e-6)
    assert feats["BB000002"].velocity_window_days == 90
    assert feats["BB000002"].daily_velocity == pytest.approx(4 / 90, rel=1e-6)
    assert feats["BB000004"].velocity_window_days == 0
    assert feats["BB000004"].daily_velocity is None


def test_stock_unknown_is_not_zero(tables, cfg):
    f = _features(tables, cfg)["BB000004"]
    assert f.inventory_joined is False
    assert f.current_sellable is None
    assert f.availability == "UNAVAILABLE_STOCK"


def test_reserved_badge_and_effective_stock(tables, cfg):
    f = _features(tables, cfg)["BB000001"]
    assert f.availability == "RESERVED"
    assert f.current_sellable == 0
    assert f.older_incoming == 3  # EK proxy: AS_OF-2/-4/-6 in window, AS_OF-40 not
    assert f.purchased_today == 0
    assert f.effective_stock == 3


def test_margin_and_history_and_profit_status(tables, cfg):
    f = _features(tables, cfg)["BB000001"]
    assert f.margin_pct == pytest.approx(40.0, abs=1e-6)
    assert f.hist_success == pytest.approx(1.0)
    assert f.profitability.status == "CONFIRMED"
    assert f.profitability.expected_ek == pytest.approx(80.0)


def test_merge_redirect_rolls_sales_to_survivor(make_tables, cfg):
    old_sales = sales_rows(
        "BB000099",
        "PS4",
        "PS4 SLIM 1TB",
        [(AS_OF - timedelta(days=i * 2), 2, 0.4) for i in range(1, 9)],
    )
    tables = make_tables(
        products=[
            ("BB000010", "PS4", "PS4 SLIM 1TB", "JA"),
            ("BB000011", "PS4", "PS4 PRO 1TB", "JA"),
        ],
        sales=old_sales,
        inventory=[("BB000010", 0, 0, 0, "PARSER_PIPELINE", "OUT_OF_STOCK")],
        mapping=[("BB000010", "PARSER_PIPELINE", "KATEGORIE_UND_MODELLSCHLUESSEL", "JA")],
        merges=[
            {"Alte Produkt-ID": "BB000099", "Ziel-Produkt-ID": "BB000010", "Status": "AUSGEFUEHRT"}
        ],
    )
    r = build_merge_redirect(tables.zusammenfuehrung)
    tables.tagesprofite["produkt_id"] = tables.tagesprofite["produkt_id"].map(lambda p: r.get(p, p))
    feats = _features(tables, cfg)
    assert feats["BB000010"].units_30d == 16
    assert feats["BB000010"].velocity_window_days == 30
