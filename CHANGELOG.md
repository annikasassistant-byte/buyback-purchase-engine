# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Checked the interim sample dataset (`BuyBack - Profit (Aktualisiert
  2026-09-02).xlsx`) into the repository under
  `data/raw/full_dataset_2026_run/` as a versioned fixture — see
  [ADR&nbsp;0007](docs/adr/0007-ship-the-interim-sample-dataset.md). A fresh
  clone can now run the engine and the golden test with no manual setup.
- `find_default_workbook()` / `DEFAULT_WORKBOOK_GLOB` centralised in
  `adapters/workbook.py` (previously duplicated in the CLI and the golden
  test); added `tests/unit/test_workbook.py`.
- **Confidence Score evidence-breadth penalty** — see
  [ADR&nbsp;0008](docs/adr/0008-confidence-reflects-purchase-score-evidence-breadth.md).
  A product whose Purchase Score rests on only 1 or 2 of its 3 "real"
  components (demand / inventory need / profit) now has Confidence docked
  explicitly for that (`confidence.evidence_breadth_penalty` in
  `config/engine.yml`, default 15 / 5 points), surfaced as a named risk line.
  Closes a gap between ADR 0003's stated intent ("that same uncertainty does
  lower the Confidence Score") and what the code actually did: the
  single-component-cap case could previously still read as moderately
  confident if its mapping and inventory-join happened to be clean.
  `ConfidenceBreakdown` gains `evidence_components_present` and
  `evidence_penalty`; `ConfidenceScorer.score()` now also takes the product's
  `ScoreBreakdown`.

### Changed

- `.gitignore` now allow-lists exactly that one file under `data/raw/` —
  everything else dropped there (fresh exports, the live purchase table)
  stays ignored by default.
- Regenerated the golden fixture for the evidence-breadth penalty above.
  All 374 Purchase Scores and BUY/CONSIDER/SKIP labels are unchanged (this
  only touches Confidence); 193 of 374 products' Confidence dropped, by at
  most 15 points.

## [0.2.0] - 2026-09-03

Phase 2 MVP.

### Added

- **Feature calculation** (`pipeline/features.py`): sales velocity (30/90-day
  window with switch), trailing margin, historical success rate, Effective Stock
  Position (`Verfügbar + PurchasedToday + OlderIncoming`, three terms kept
  separate), days-of-supply, availability badge, mapping-quality resolution,
  executed-merge redirect (SCD-lite).
- **Purchase Score** (`pipeline/scoring.py`): weighted additive model
  (35/30/25/10), proportional redistribution of missing components,
  single-component cap, overstock / slow-mover penalty. Config-driven.
- **Confidence Score** (`pipeline/confidence.py`): independent 0-100 %
  (mapping / sales-sufficiency / inventory-reliability / profitability-
  reliability). Never multiplied with the Purchase Score.
- **Quantity + budget allocation** (`pipeline/quantity.py`): periodic-review
  order-up-to-level quantity, per-SKU / per-category exposure cap, greedy
  daily-budget allocation ranked by expected gross profit per euro.
- **Explanation generator** (`pipeline/explain.py`): deterministic
  `reasons[]` + `risks[]`, each line traceable to a single feature.
- **Append-only history** (`adapters/store.py`): `FileStore` (JSONL + latest
  snapshot) and optional `SqliteStore`, behind a `RecommendationStore` port.
- **Seams**: `Profitability` port with `TrailingWindowProfitability`;
  `IncomingStockSource` port with `EkNormalisiertIncoming` (proxy) and a
  `LivePurchaseTableIncoming` stub; `ParserWorkbook` reader.
- **Config as data**: `config/engine.yml`, validated every run; `config_hash`
  stamped on output.
- Tooling: Ruff, mypy, pytest + coverage, pre-commit, GitHub Actions CI.
- Docs: `docs/architecture.md`, ADRs 0001-0006.

### Notes

- No accuracy figure is claimed for the engine; the append-only history is the
  dataset for the forward-looking backtest.
