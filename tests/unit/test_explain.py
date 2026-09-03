from __future__ import annotations

from purchase_engine.pipeline.confidence import ConfidenceScorer
from purchase_engine.pipeline.explain import ExplanationGenerator
from purchase_engine.pipeline.quantity import QuantityPlanner
from purchase_engine.pipeline.scoring import PurchaseScorer
from tests._factories import mkfeat


def _explain(cfg, f):
    sb = PurchaseScorer(cfg).score_all([f])[f.produkt_id]
    cb = ConfidenceScorer(cfg).score(f)
    qp = QuantityPlanner(cfg).plan(f)
    return ExplanationGenerator(cfg).generate(f, sb, cb, qp)


def test_reasons_and_risks_always_returned_together(cfg):
    pos, risk = _explain(cfg, mkfeat("BB1"))
    assert isinstance(pos, list) and isinstance(risk, list)
    assert any("last 30 days" in p or "last 90 days" in p for p in pos)
    assert any("market weight redistributed" in p for p in pos)


def test_reserved_state_is_explained_not_called_empty(cfg):
    pos, _ = _explain(cfg, mkfeat("BB1", avail="RESERVED", on_hand=1.0))
    assert any("reserved in open orders" in p for p in pos)


def test_incoming_units_flagged_as_double_count_guard(cfg):
    _, risk = _explain(cfg, mkfeat("BB1", older_incoming=5))
    assert any("purchased in the last" in r and "Effective Stock" in r for r in risk)


def test_unknown_stock_is_flagged_not_treated_as_zero(cfg):
    _, risk = _explain(cfg, mkfeat("BB1", joined=False, sellable=None, avail="UNAVAILABLE_STOCK"))
    assert any("no inventory row" in r and "not zero" in r for r in risk)


def test_fallback_mapping_is_surfaced_as_the_confidence_lever(cfg):
    _, risk = _explain(cfg, mkfeat("BB1", mquelle="KATEGORIE_UND_MODELLSCHLUESSEL"))
    assert any("category+model fallback" in r for r in risk)


def test_explanations_are_capped_at_five_lines_each(cfg):
    pos, risk = _explain(
        cfg,
        mkfeat(
            "BB1", older_incoming=5, dup=True, margin=-30.0, prof_status="UNAVAILABLE", ok_rows=0
        ),
    )
    assert len(pos) <= 5
    assert len(risk) <= 5
