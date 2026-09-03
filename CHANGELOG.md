# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
