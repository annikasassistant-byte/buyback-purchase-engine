from __future__ import annotations

import pytest

from purchase_engine.pipeline.confidence import ConfidenceScorer
from purchase_engine.pipeline.scoring import PurchaseScorer
from tests._factories import mkfeat, mkscore


def test_mapping_tiers(cfg):
    cs = ConfidenceScorer(cfg)
    full = mkscore()  # 3/3 components present - isolate the mapping dimension
    assert cs.score(mkfeat("a", mquelle="ALIAS"), full).mapping == 100
    assert cs.score(mkfeat("a", mquelle="EINDEUTIGER_MODELLSCHLUESSEL"), full).mapping == 100
    assert cs.score(mkfeat("a", mquelle="ARTIKELNUMMER_OVERRIDE_X"), full).mapping == 90
    assert cs.score(mkfeat("a", mquelle="KATEGORIE_UND_MODELLSCHLUESSEL"), full).mapping == 70
    assert cs.score(mkfeat("a", dup=True), full).mapping == 40


def test_confidence_independent_of_optional_market_signal(cfg):
    conf = ConfidenceScorer(cfg).score(mkfeat("BB1"), mkscore())
    w = cfg.confidence.weights
    expect = (
        w["mapping"] * conf.mapping
        + w["sales_sufficiency"] * conf.sales_sufficiency
        + w["inventory_reliability"] * conf.inventory_reliability
        + w["profitability_reliability"] * conf.profitability_reliability
    )
    # mkscore() defaults to full 3/3 coverage -> no evidence penalty, so the
    # four weighted dimensions alone should reproduce the total exactly.
    assert conf.evidence_penalty == 0
    assert conf.confidence == round(expect)


def test_score_and_confidence_are_not_multiplied(cfg):
    f = mkfeat(
        "BB1",
        u90=1.0,
        dss=200,
        ok_rows=1,
        prof_status="TEMP_CALCULATED",
        mquelle="KATEGORIE_UND_MODELLSCHLUESSEL",
        vel=3.0,
        u30=90.0,
        sellable=0.0,
    )
    sb = PurchaseScorer(cfg).score_all([f])["BB1"]
    cb = ConfidenceScorer(cfg).score(f, sb)
    assert sb.score >= 60
    assert cb.confidence <= 75
    assert sb.score != pytest.approx(sb.score * cb.confidence / 100)


def test_unmatched_inventory_zeroes_only_the_inventory_dimension(cfg):
    cb = ConfidenceScorer(cfg).score(
        mkfeat("BB1", joined=False, sellable=None, avail="UNAVAILABLE_STOCK"), mkscore()
    )
    assert cb.inventory_reliability == 0
    assert cb.mapping > 0 and cb.sales_sufficiency > 0


# --------------------------------------------------------------------------- #
# Evidence-breadth penalty (ADR 0008)                                        #
# --------------------------------------------------------------------------- #
def test_full_coverage_has_no_evidence_penalty(cfg):
    cb = ConfidenceScorer(cfg).score(
        mkfeat("BB1"), mkscore(demand=80, inventory_need=80, profit=80)
    )
    assert cb.evidence_components_present == 3
    assert cb.evidence_penalty == 0


def test_market_never_counts_toward_evidence_breadth(cfg):
    """Market is always None in the MVP by design - it must never look like
    'missing evidence' the way an absent demand/inventory/profit signal does."""
    cb = ConfidenceScorer(cfg).score(
        mkfeat("BB1"), mkscore(demand=80, inventory_need=80, profit=80, market=None)
    )
    assert cb.evidence_components_present == 3
    assert cb.evidence_penalty == 0


def test_single_component_docks_more_than_two(cfg):
    one = ConfidenceScorer(cfg).score(
        mkfeat("BB1"), mkscore(demand=80, inventory_need=None, profit=None)
    )
    two = ConfidenceScorer(cfg).score(
        mkfeat("BB1"), mkscore(demand=80, inventory_need=80, profit=None)
    )
    three = ConfidenceScorer(cfg).score(mkfeat("BB1"), mkscore())

    assert one.evidence_components_present == 1
    assert two.evidence_components_present == 2
    assert three.evidence_components_present == 3

    assert one.evidence_penalty == pytest.approx(
        cfg.confidence.evidence_breadth_one_component_penalty
    )
    assert two.evidence_penalty == pytest.approx(
        cfg.confidence.evidence_breadth_two_components_penalty
    )
    assert three.evidence_penalty == 0

    assert one.evidence_penalty >= two.evidence_penalty > three.evidence_penalty
    # same underlying feature, only the score's evidence breadth differs -
    # confidence must fall as evidence thins out.
    assert one.confidence <= two.confidence <= three.confidence


def test_single_component_cap_always_triggers_an_evidence_penalty(cfg):
    """The exact scenario the design is for: a product where the Purchase
    Score is capped because only one component survived (the "Canon 18-55mm"
    case) must also show up as reduced Confidence - not just incidentally
    through sales_sufficiency, but as an explicit, named signal."""
    hi = mkfeat("HI", vel=None, win=0, u30=0, u90=0, dss=None, margin=80.0, ok_rows=9, hist=1.0)
    lo = mkfeat("LO", vel=None, win=0, u30=0, u90=0, dss=None, margin=5.0, ok_rows=9, hist=1.0)
    sb = PurchaseScorer(cfg).score_all([hi, lo])["HI"]
    assert sb.single_component_capped is True  # sanity: this is that scenario

    cb = ConfidenceScorer(cfg).score(hi, sb)
    assert cb.evidence_components_present == 1
    assert cb.evidence_penalty == pytest.approx(
        cfg.confidence.evidence_breadth_one_component_penalty
    )
    assert cb.evidence_penalty > 0


def test_evidence_penalty_never_pushes_confidence_below_zero(cfg):
    f = mkfeat(
        "BB1",
        joined=False,
        sellable=None,
        avail="UNAVAILABLE_STOCK",
        mquelle="",
        u90=0.0,
        dss=None,
        prof_status="UNAVAILABLE",
        ok_rows=0,
    )
    cb = ConfidenceScorer(cfg).score(f, mkscore(demand=None, inventory_need=None, profit=None))
    assert 0 <= cb.confidence <= 100
