# Architecture

The engine is the bottom band of the diagram in
`docs/BuyBack Purchase Engine.html` (the Technical Implementation Plan). It reads
the existing parser's **output** and produces an explainable daily buy list. It
never re-parses a product name and never writes to the parser's sheets.

## Layering (hexagonal / ports-and-adapters)

```
            ┌───────────────────────────┐   ┌───────────────────────────┐
  cli.py    │ argparse, logging,         │   │ api/  FastAPI: trigger a   │  <- driving
            │ the printed report         │   │ run, live budget re-alloc, │     adapters,
            └──────────────┬─────────────┘   │ BUY/ADJUST/SKIP logging    │     two peers
                            │ builds + injects└──────────────┬─────────────┘
                            │ adapters, calls Engine.run() ──┘ (+ adapters/query.py, read-only)
            ┌───────────────▼─────────────────────────────────────────┐
  pipeline/ │  FeatureBuilder → PurchaseScorer / ConfidenceScorer     │
            │  → QuantityPlanner → BudgetAllocator → ExplanationGen    │
            │  orchestrator.Engine wires them together                 │
            └───────────────┬─────────────────────────────────────────┘
                            │ depends only on domain + config
            ┌───────────────▼─────────────────────────────────────────┐
  domain/   │  models.py  - dataclasses (ProductFeatures, …)          │
            │  ports.py   - Protocols: Profitability,                  │
            │               IncomingStockSource, RecommendationStore   │
            └───────────────▲─────────────────────────────────────────┘
                            │ implemented by
            ┌───────────────┴─────────────────────────────────────────┐
  adapters/ │  workbook.py       - read the "BuyBack - Profit" xlsx    │
            │  incoming.py       - EK_Normalisiert proxy (+ LPT stub)  │
            │  profitability.py  - TrailingWindowProfitability         │
            │  store.py          - FileStore / SqliteStore / PostgresStore │
            │  query.py          - Postgres reads for api/ only         │
            └─────────────────────────────────────────────────────────┘
```

**Import rule (enforced by review, and `ruff` isort grouping):** imports point
*inward* only. `domain` imports nothing from the project. `pipeline` imports
`domain` + `config`. `adapters` import `domain` + `config`. `cli` and `api`
may import anything - they're peers, both driving adapters, neither imports
the other. `adapters/query.py` (read-only, API-specific) is imported only by
`api/`, never by `pipeline` or `cli`.

## Data flow for one run

1. **`cli.main`** parses args, configures logging, resolves the workbook path
   (searching upward from the CWD), builds a `FileStore` (+ optional
   `SqliteStore`) and calls `Engine.run`.
2. **`Engine.run`**
   - `ParserWorkbook.load()` → `ParserTables` (7 canonicalised sheets).
   - Applies the executed-merge redirect to `Tagesprofite.produkt_id` (SCD-lite).
   - Resolves `as_of` (config, else latest sale date).
   - Builds the incoming-stock source and `TrailingWindowProfitability`.
   - `FeatureBuilder.build(as_of)` → one `ProductFeatures` per active product.
   - `PurchaseScorer.score_all`, then `ConfidenceScorer.score_all(features,
     scores)` - Confidence reads *which* score components had data (evidence
     breadth, ADR 0008), never the score's *value*. Still never multiplied or
     combined into one number.
   - `QuantityPlanner.plan_all` → order-up-to-level quantities.
   - Labels assigned from score thresholds (BUY / CONSIDER / SKIP).
   - `BudgetAllocator.allocate` rations the BUY tier by expected GP per euro.
   - `ExplanationGenerator.generate` → `reasons[]` + `risks[]` per product.
   - Assembles `RecommendationSet`, sorts (funded BUYs first), persists it.

## The three ports

| Port | Contract | MVP adapter | Later |
|---|---|---|---|
| `Profitability` | `get_profitability(pid, as_of) -> ProductProfitability` (6 fixed fields, fixed status vocabulary) | `TrailingWindowProfitability` | `ProfitEngineClient` reading a shared `product_profitability` table |
| `IncomingStockSource` | `counts(as_of) -> IncomingCounts` (per-model-key `purchased_today` + `older_incoming`) | `EkNormalisiertIncoming` (proxy) | `LivePurchaseTableIncoming` once the hand-entered sheet is wired through the parser |
| `RecommendationStore` | `save(RecommendationSet) -> None` | `FileStore` (JSONL) / `SqliteStore` / `PostgresStore` (`engine_run` + `recommendation`, ADR 0009) | full `dim_product` SCD2 - the merge-redirect-on-read approach may turn out sufficient, see ADR 0009 |

Scoring code never branches on `ProductProfitability.source` - swapping the
adapter changes only that string.

## `api/` - the second driving adapter (ADR 0010)

`cli.py` and `api/` both build adapters and call into `pipeline/orchestrator.Engine`
- neither is "the" entry point, they're peers. `api/` additionally depends on
`adapters/query.py` (read-only Postgres queries the CLI never needs: latest
run, live budget re-allocation via a reconstructed call into the real
`BudgetAllocator`, buyer-action logging) - that module is never imported by
`pipeline` or `domain`, so the one-way dependency rule still holds; it's a
second read path bolted onto the adapters layer, not a hole in it.

## What is deliberately *not* here

The buyer UI itself (a separate repository - `api/` is what it calls, not the
UI), the JTL API, Keepa / Back Market signals, a real
`PURCHASED → INCOMING → RECEIVED` ledger, full `dim_product` SCD2. Each has a
named seam above.

## Decisions

See [`adr/`](adr/). New decisions that change the architecture, a scoring
formula, or a data contract get an ADR (copy `0001` as the template) plus a
`CHANGELOG.md` entry.
