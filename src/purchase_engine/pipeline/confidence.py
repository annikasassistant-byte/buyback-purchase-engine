"""Confidence Score (0-100 %).

Fully independent of the Purchase Score - never multiplied. Answers "how much do
we trust the inputs behind this recommendation", per product.

::

    Confidence =
        0.30 * MappingConfidence
      + 0.25 * SalesDataSufficiency
      + 0.25 * InventoryDataReliability
      + 0.20 * ProfitabilityReliability
      - EvidenceBreadthPenalty

External-market availability is deliberately excluded: an optional signal that
isn't built yet must not deflate confidence.

The four weighted dimensions above each answer "how much do we trust *this one
source*". They deliberately don't answer a different question: "how much of
the Purchase Score's own evidence actually showed up for this product at all".
A product resting on a single surviving score component (see
``score.single_component_cap`` in ``pipeline/scoring.py``) is a materially
weaker basis for a recommendation than one corroborated by three independent
signals, even if every source it *does* have is individually reliable.
``EvidenceBreadthPenalty`` makes that gap explicit rather than incidental - see
docs/adr/0008.
"""

from __future__ import annotations

from purchase_engine.config import EngineConfig
from purchase_engine.domain.models import ConfidenceBreakdown, ProductFeatures, ScoreBreakdown

_UNIQUE_KEYS = {"EINDEUTIGER_MODELLSCHLUESSEL"}
_ALIAS_KEYS = {"ALIAS"}
_FALLBACK_KEYS = {"KATEGORIE_UND_MODELLSCHLUESSEL"}
_REWEIGHT_KEYS = {"NEUBEWERTUNG_OHNE_EINDEUTIGEN_TREFFER"}


class ConfidenceScorer:
    def __init__(self, cfg: EngineConfig) -> None:
        self.cfg = cfg

    def _mapping(self, f: ProductFeatures) -> float:
        p = self.cfg.confidence.mapping_points
        if f.is_duplicate:
            return p.get("duplicate", 40.0)
        q = (f.mapping_quelle or "").upper()
        if q in _ALIAS_KEYS:
            return p.get("alias", 100.0)
        if q in _UNIQUE_KEYS:
            return p.get("unique_key", 100.0)
        if "OVERRIDE" in q:
            return p.get("override", 90.0)
        if q in _FALLBACK_KEYS:
            return p.get("fallback", 70.0)
        if q in _REWEIGHT_KEYS:
            return p.get("reweighted_no_match", 60.0)
        if q:  # some other non-empty parser source
            return p.get("fallback", 70.0)
        if f.join_source == "ALIAS":
            return p.get("alias", 100.0)
        if f.join_source == "VERKAUFS_MAPPING":
            return p.get("fallback", 70.0)
        return p.get("unknown", 50.0)

    def _sales_sufficiency(self, f: ProductFeatures) -> float:
        s = self.cfg.confidence
        units = min(f.units_90d / s.units_target, 1.0) * s.units_points if s.units_target else 0.0
        dss = f.days_since_sale
        if dss is None:
            rec = 0.0
        elif dss <= s.recent_days:
            rec = s.recency_points
        elif dss >= s.stale_days:
            rec = 0.0
        else:
            span = max(s.stale_days - s.recent_days, 1e-9)
            rec = s.recency_points * (1.0 - (dss - s.recent_days) / span)
        return max(0.0, min(100.0, units + rec))

    @staticmethod
    def _inventory_reliability(f: ProductFeatures) -> float:
        # 100 if the SKU joins to the inventory CSV, 0 if it doesn't (stock then
        # = UNAVAILABLE, never zero). The live-purchase-table haircut the plan
        # once carried here is gone.
        return 100.0 if f.inventory_joined else 0.0

    def _profitability_reliability(self, f: ProductFeatures) -> float:
        pts = self.cfg.confidence.profitability_status_points
        return float(pts.get(f.profitability.status, 0.0))

    def _evidence_breadth_penalty(self, score: ScoreBreakdown) -> tuple[int, float]:
        """How many of the Purchase Score's 3 "real" components had data, and
        the points to dock for that. Market is excluded - it's UNAVAILABLE for
        every product in the MVP by design, not evidence about this one."""
        present = sum(x is not None for x in (score.demand, score.inventory_need, score.profit))
        c = self.cfg.confidence
        penalty = {
            0: c.evidence_breadth_one_component_penalty,  # degenerate: score itself is 0
            1: c.evidence_breadth_one_component_penalty,
            2: c.evidence_breadth_two_components_penalty,
        }.get(present, 0.0)
        return present, penalty

    def score(self, f: ProductFeatures, score: ScoreBreakdown) -> ConfidenceBreakdown:
        w = self.cfg.confidence.weights
        m = self._mapping(f)
        s = self._sales_sufficiency(f)
        i = self._inventory_reliability(f)
        p = self._profitability_reliability(f)
        present, penalty = self._evidence_breadth_penalty(score)
        conf = (
            w["mapping"] * m
            + w["sales_sufficiency"] * s
            + w["inventory_reliability"] * i
            + w["profitability_reliability"] * p
            - penalty
        )
        return ConfidenceBreakdown(
            mapping=round(m, 1),
            sales_sufficiency=round(s, 1),
            inventory_reliability=round(i, 1),
            profitability_reliability=round(p, 1),
            evidence_components_present=present,
            evidence_penalty=round(penalty, 1),
            confidence=round(max(0.0, min(100.0, conf))),
        )

    def score_all(
        self, features: list[ProductFeatures], scores: dict[str, ScoreBreakdown]
    ) -> dict[str, ConfidenceBreakdown]:
        return {f.produkt_id: self.score(f, scores[f.produkt_id]) for f in features}
