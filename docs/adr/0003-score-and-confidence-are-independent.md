# 3. Purchase Score and Confidence Score are independent

Date: 2026-09-03

## Status

Accepted

## Context

Two different questions matter for a buy decision: *how good does this look?*
(demand, stock need, profit) and *how much do we trust the inputs?* (mapping
quality, sales history depth, whether stock joined, profit-data reliability).

Collapsing them into one number - e.g. `score * confidence` - hides uncertainty
exactly when it matters most, and would let a data gap silently exclude a
genuinely attractive product. The stakeholder asked for the two to be kept
separate and for uncertainty to be *visible*.

## Decision

- `PurchaseScorer` produces a 0-100 Purchase Score. `ConfidenceScorer` produces
  an independent 0-100 Confidence Score. They are computed side by side and
  **never multiplied or combined**.
- Missing Purchase Score components are **redistributed** proportionally over the
  surviving components, never zero-filled. An unbuilt optional signal (Market /
  Keepa) therefore never lowers the score.
- That same uncertainty *does* lower the Confidence Score.
- One guard against the redistribution being misleading: a score resting on a
  single surviving component is capped (`score.single_component_cap`, default
  70) rather than shown as a bare 100.
- No hard profitability gate: a product is never auto-excluded for uncertain
  profit; it scores lower and/or flags lower confidence.

## Consequences

- The buyer sees `score 90 / confidence 69%` and understands "strong signal,
  thin evidence" - the intended decision-support shape.
- Recommendations must always be presented with both numbers and with the
  `reasons[]` + `risks[]` explanation; showing the score alone is a UI bug.
- Tests assert the independence (`test_confidence.py`) and the redistribution /
  cap (`test_scoring.py`).
