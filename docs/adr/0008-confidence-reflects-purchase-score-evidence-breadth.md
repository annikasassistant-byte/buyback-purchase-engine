# 8. Confidence reflects how much of the Purchase Score's own evidence was present

Date: 2026-09-03

## Status

Accepted

## Context

ADR 0003 states: "Missing Purchase Score components are redistributed
proportionally over the surviving components, never zero-filled ... That same
uncertainty does lower the Confidence Score." In practice this held only
incidentally, not by construction:

- When **demand** drops out of the score (no sales in the trailing windows),
  Confidence's `sales_sufficiency` dimension also collapses to (near) zero -
  the two are driven by the same `units_90d` / recency numbers, so this case
  was already correctly covered.
- When **profit** drops out (`ok_rows == 0`), Confidence's
  `profitability_reliability` also goes to zero - `ProductProfitability.status`
  is `UNAVAILABLE` for exactly the same condition, so this case was correctly
  covered too.
- When **inventory need** drops out, though, it can do so for a reason
  Confidence's `inventory_reliability` dimension does not track: `InventoryNeed`
  requires *both* `inventory_joined` *and* a non-zero velocity window
  (`pipeline/scoring.py`). `inventory_reliability` only checks the first
  condition. A product with perfectly good, joined stock data but no sales
  history has `inventory_reliability = 100` even though `InventoryNeed` has
  silently dropped out of its Purchase Score for the exact same "no sales
  history" reason that already zeroed out `sales_sufficiency`.

So a product could rest on a single surviving score component (triggering
`score.single_component_cap`, see the "Canon 18-55mm" case in ADR 0003) while
still reading as decently confident - if its mapping and inventory-join
happened to be clean, the resulting Confidence looked more like a 60-75% than
a "we're building this recommendation on one leg" number.

This is a real, distinct failure mode. It's not that any of the four existing
confidence dimensions is *wrong* - each answers "how much do I trust this one
source" correctly. None of them answers "how many independent sources actually
went into this score at all". Those are different questions:

- **GRADE** (the medical evidence-grading framework) explicitly downgrades
  certainty for imprecise or indirect evidence, as a factor separate from each
  individual study's own risk of bias - [CDC ACIP GRADE handbook, ch. 8](https://www.cdc.gov/acip-grade-handbook/hcp/chapter-8-domains-decreasing-certainty-in-the-evidence/index.html).
- **Dempster-Shafer evidence theory** treats "how much is simply unknown"
  (epistemic uncertainty from missing evidence) as a first-class quantity,
  distinct from conflict or unreliability between the sources you do have -
  [ScienceDirect overview](https://www.sciencedirect.com/topics/computer-science/dempster-shafer-theory).
- **MCDA** literature treats missing/incomplete criteria as a distinct problem
  from criteria-weight validity, with dedicated methods (e.g. SMAA) for it -
  [1000minds, MCDA overview](https://www.1000minds.com/decision-making/what-is-mcdm-mcda); [Stochastic MCA, Wikipedia](https://en.wikipedia.org/wiki/Stochastic_multicriteria_acceptability_analysis).

All three independently support the same conclusion: evidence *breadth*
(how much you have) deserves to be measured, separately from evidence
*reliability* (how much you trust what you have).

## Decision

Add an explicit, config-driven **evidence-breadth penalty** to the Confidence
Score, subtracted from the weighted blend of the four existing dimensions -
the same additive-adjustment pattern the Purchase Score already uses for its
own overstock penalty:

```
Confidence = 0.30·Mapping + 0.25·SalesSufficiency + 0.25·InventoryReliability
           + 0.20·ProfitabilityReliability − EvidenceBreadthPenalty
```

- Counts how many of the Purchase Score's 3 "real" components (demand,
  inventory need, profit) actually had data for this product. **Market is
  excluded from the count** - it is `UNAVAILABLE` for every product in the
  MVP by design (no Keepa/Back Market integration yet), so its absence is not
  evidence about *this specific product*, it would just uniformly dock every
  recommendation's confidence for a fact that's true of all of them.
- 3 of 3 present → no penalty. 2 of 3 → a small penalty
  (`confidence.evidence_breadth_penalty.two_components`, default 5). 1 of 3 or
  0 of 3 (the degenerate single-component-cap case) → a larger penalty
  (`...one_component`, default 15). Both are config, not code.
- `ConfidenceBreakdown` gains `evidence_components_present` (0-3) and
  `evidence_penalty` (the points actually docked), and the explanation
  generator surfaces a dedicated risk line when the penalty is non-zero -
  naming the reason, not just lowering a number silently.
- The plumbing: `ConfidenceScorer.score()` now takes the product's
  `ScoreBreakdown` alongside its `ProductFeatures`
  (`ConfidenceScorer.score_all(features, scores)`), computed after
  `PurchaseScorer.score_all()` in `Engine.run()`. This does **not** weaken the
  "never multiplied" rule in ADR 0003: Confidence still never multiplies or
  otherwise combines with the Purchase Score's *value* - it only reads which
  of the score's *components* had data, which is exactly the information the
  redistribution rule already promised would "show up in Confidence instead".

## Consequences

- Regenerated the golden fixture: all 374 Purchase Scores and BUY/CONSIDER/SKIP
  labels are byte-identical to before (this touches Confidence only); 193 of
  374 products' Confidence dropped, by at most the configured 15-point cap,
  concentrated in CONSIDER/SKIP-tier products with thin sales history - see
  `tests/golden/data/summary.json`.
- A product resting on one signal now reads as measurably less confident than
  an otherwise-identical product backed by three, even when every individual
  source it has is itself clean - closing the gap between ADR 0003's stated
  intent and what the code actually did.
- New unit tests (`tests/unit/test_confidence.py`) lock in the coupling
  directly: `single_component_capped` on the score always implies
  `evidence_penalty > 0` on confidence for that same product.
