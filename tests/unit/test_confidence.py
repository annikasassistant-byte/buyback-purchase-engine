from __future__ import annotations

import pytest

from purchase_engine.pipeline.confidence import ConfidenceScorer
from purchase_engine.pipeline.scoring import PurchaseScorer
from tests._factories import mkfeat


def test_mapping_tiers(cfg):
    cs = ConfidenceScorer(cfg)
    assert cs.score(mkfeat("a", mquelle="ALIAS")).mapping == 100
    assert cs.score(mkfeat("a", mquelle="EINDEUTIGER_MODELLSCHLUESSEL")).mapping == 100
    assert cs.score(mkfeat("a", mquelle="ARTIKELNUMMER_OVERRIDE_X")).mapping == 90
    assert cs.score(mkfeat("a", mquelle="KATEGORIE_UND_MODELLSCHLUESSEL")).mapping == 70
    assert cs.score(mkfeat("a", dup=True)).mapping == 40


def test_confidence_independent_of_optional_market_signal(cfg):
    conf = ConfidenceScorer(cfg).score(mkfeat("BB1"))
    w = cfg.confidence.weights
    expect = (
        w["mapping"] * conf.mapping
        + w["sales_sufficiency"] * conf.sales_sufficiency
        + w["inventory_reliability"] * conf.inventory_reliability
        + w["profitability_reliability"] * conf.profitability_reliability
    )
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
    cb = ConfidenceScorer(cfg).score(f)
    assert sb.score >= 60
    assert cb.confidence <= 75
    assert sb.score != pytest.approx(sb.score * cb.confidence / 100)


def test_unmatched_inventory_zeroes_only_the_inventory_dimension(cfg):
    cb = ConfidenceScorer(cfg).score(
        mkfeat("BB1", joined=False, sellable=None, avail="UNAVAILABLE_STOCK")
    )
    assert cb.inventory_reliability == 0
    assert cb.mapping > 0 and cb.sales_sufficiency > 0
