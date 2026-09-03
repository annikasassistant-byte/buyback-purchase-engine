"""Property-style checks on the whole pipeline, run on synthetic tables."""

from __future__ import annotations

from datetime import timedelta

from purchase_engine.pipeline.orchestrator import Engine
from tests._factories import AS_OF, build_tables, sales_rows


def _run(cfg, tables, **kw):
    return Engine(cfg).run(tables=tables, as_of=AS_OF, **kw)


def _decisions(result) -> dict[str, tuple]:
    return {
        r.produkt_id: (r.label, r.purchase_score, r.confidence, r.recommended_qty)
        for r in result.recommendations
    }


def _buy_spend(result) -> float:
    return sum((r.est_total_cost or 0) for r in result.by_label("BUY"))


def test_pipeline_is_idempotent(cfg):
    assert _decisions(_run(cfg, build_tables())) == _decisions(_run(cfg, build_tables()))


def test_more_velocity_never_lowers_the_score(cfg):
    products = [("BB000001", "PS4", "PS4 SLIM 1TB", "JA"), ("BB000002", "PS4", "PS4 PRO 1TB", "JA")]
    inv = [
        ("BB000001", 0, 0, 0, "PARSER_PIPELINE", "OUT_OF_STOCK"),
        ("BB000002", 0, 0, 0, "PARSER_PIPELINE", "OUT_OF_STOCK"),
    ]
    mp = [
        ("BB000001", "PARSER_PIPELINE", "KATEGORIE_UND_MODELLSCHLUESSEL", "JA"),
        ("BB000002", "PARSER_PIPELINE", "KATEGORIE_UND_MODELLSCHLUESSEL", "JA"),
    ]

    def score_for(n_units: int) -> int:
        sales = sales_rows(
            "BB000001", "PS4", "PS4 SLIM 1TB", [(AS_OF - timedelta(days=2), n_units, 0.4)]
        ) + sales_rows("BB000002", "PS4", "PS4 PRO 1TB", [(AS_OF - timedelta(days=2), 5, 0.4)])
        res = _run(cfg, build_tables(products=products, sales=sales, inventory=inv, mapping=mp))
        return next(r.purchase_score for r in res.recommendations if r.produkt_id == "BB000001")

    assert score_for(60) >= score_for(4)


def test_every_recommendation_carries_an_explanation(cfg):
    for r in _run(cfg, build_tables()).recommendations:
        assert r.reasons, f"{r.produkt_id} has no reasons"
        assert r.label in {"BUY", "CONSIDER", "SKIP"}
        assert 0 <= r.purchase_score <= 100
        assert 0 <= r.confidence <= 100


def test_budget_cap_never_increases_total_spend(cfg):
    capped = _buy_spend(_run(cfg, build_tables(), budget_eur=100.0))
    uncapped = _buy_spend(_run(cfg, build_tables()))
    assert capped <= uncapped + 1e-6


def test_run_needs_a_source(cfg):
    try:
        Engine(cfg).run()
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
