# BuyBack Purchase Engine

**Phase 2 MVP** — feature calculation · Purchase Score · Confidence Score ·
quantity + budget allocation · explanation generator.

A daily decision-support answer to one question: *what should we buy right now,
how much, and how sure are we?* It reads the existing product-matching parser's
**output** read-only and produces an explainable, ranked buy list. It never
re-parses a product name and never writes back to the parser
([ADR&nbsp;0002](docs/adr/0002-read-only-on-the-parser.md)).

Implements Phase 2 of the Technical Implementation Plan
(`docs/BuyBack Purchase Engine.html` in the analysis repo). Out of scope here:
buyer UI / BUY-ADJUST-SKIP logging (Phase 3), the JTL API, Keepa / Back Market
signals, the real Profit Engine, a `PURCHASED→INCOMING→RECEIVED` ledger,
Postgres — each has a named seam.

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
make install          # editable install + dev tools + pre-commit hooks

python -m purchase_engine --budget 1500              # run against an auto-discovered workbook
```

`uv` works too: `uv sync --extra dev`.

The one input the MVP needs is the **`BuyBack - Profit` xlsx** (the parser's
offline export). By default the CLI searches upward from the working directory
for `data/raw/full_dataset_2026_run/BuyBack - Profit*.xlsx`; pass `--workbook`
to point at it explicitly. The Phase-1 inventory join is already baked into that
workbook as the `Inventar_Bestand` tab.

```bash
python -m purchase_engine --workbook "/path/to/BuyBack - Profit ....xlsx" \
    --as-of 2026-08-24 --budget 1500
python -m purchase_engine --json > run.json          # machine-readable
python -m purchase_engine --sqlite                   # also mirror history to SQLite
purchase-engine --help                               # console script (after install)
```

Every run appends to `./artifacts/` (override with `--artifacts`):

| file | one row per | purpose |
|---|---|---|
| `runs.jsonl` | engine run | metadata, counts, data-freshness |
| `recommendations.jsonl` | `(run_id, produkt_id)` | **the backtest dataset** |
| `latest.json` | — | full most-recent `RecommendationSet` (for the Phase-3 API) |

Both `.jsonl` files are append-only ([ADR&nbsp;0006](docs/adr/0006-file-based-append-only-history.md)).

---

## Repository layout

```
src/purchase_engine/
├── domain/            pure value objects + the ports the outer layers implement
│   ├── models.py        ProductFeatures, ScoreBreakdown, Recommendation, …
│   └── ports.py         Protocols: Profitability, IncomingStockSource, RecommendationStore
├── adapters/          the only layer that knows about Excel / files / SQLite
│   ├── workbook.py      ParserWorkbook — read the profit xlsx, canonical columns
│   ├── incoming.py      EkNormalisiertIncoming (proxy) + LivePurchaseTableIncoming (stub)
│   ├── profitability.py TrailingWindowProfitability (the 6-field seam)
│   └── store.py         FileStore / SqliteStore / MultiStore / NullStore
├── pipeline/          the computation; depends only on domain + config
│   ├── features.py      FeatureBuilder
│   ├── scoring.py       PurchaseScorer
│   ├── confidence.py    ConfidenceScorer
│   ├── quantity.py      QuantityPlanner + BudgetAllocator
│   ├── explain.py       ExplanationGenerator
│   └── orchestrator.py  Engine.run() — wires it together
├── config.py         load + validate config/engine.yml -> typed EngineConfig
├── errors.py         PurchaseEngineError hierarchy
├── cli.py            argparse entry point (python -m purchase_engine)
└── config/engine.yml shipped default config

tests/   unit/  property/  golden/
docs/    architecture.md  adr/0001..0006
```

Imports point inward only: `domain ← adapters ← pipeline ← cli`. See
[`docs/architecture.md`](docs/architecture.md).

---

## How a recommendation is built

```
parser workbook (read-only)
   │  Produktstamm · Tagesprofite · Inventar_Bestand · Inventar_Mapping
   │  EK_Normalisiert · Produkt_Zusammenführung · EK_Regeln
   ▼
FeatureBuilder ───────────────────────────────────────────────────────────────
   velocity 30/90d · trailing margin · historical success
   Effective Stock = Verfügbar + PurchasedToday + OlderIncoming   (3 terms, never merged)
   days-of-supply · availability badge · mapping quality · merge redirect
   profitability  ← port: TrailingWindowProfitability
   ▼
PurchaseScorer            ConfidenceScorer            QuantityPlanner
   0–100                     0–100 %                     order-up-to-level
   demand/stock/profit/      mapping/sales/inventory/    ceil(v·T − effective)
   market (redistributed)    profit reliability          capped per SKU
        └──────── never multiplied ────────┘                  │
                                                       BudgetAllocator (greedy GP/€)
   ▼
ExplanationGenerator  → reasons[] + risks[]  (always together)
   ▼
RecommendationSet  → append-only history
```

### Purchase Score (0–100) — [ADR&nbsp;0003](docs/adr/0003-score-and-confidence-are-independent.md)

```
PurchaseScore = W_demand·Demand + W_inventory·InventoryNeed + W_profit·Profit + W_market·Market
              − OverstockSlowMoverPenalty            (clipped 0..100)
```

* Weights `35 / 30 / 25 / 10` — **config**, not code ([ADR&nbsp;0004](docs/adr/0004-config-as-data.md)).
* `Demand` = category percentile of daily velocity. `Profit` = margin percentile
  ×0.7 + historical-success ×0.3.
* `InventoryNeed` = `(1 − min(days_of_supply, T)/T)·100` on **effective** stock —
  **except** when nothing is sellable today (`Verfügbar = 0`), which forces
  `InventoryNeed = 100` even with units incoming (they haven't arrived; incoming
  instead crushes the *quantity*). Plan's Galaxy A54 vs Xbox One S cases.
* Missing components are **redistributed**, never zero-filled. `Market` (Keepa /
  Back Market) is UNAVAILABLE for the MVP → its 10 points always redistribute.
* **Single-component cap**: a score built on one surviving component is capped
  (`score.single_component_cap`, default 70) — the plan's "Canon 18-55mm" fix.
* Labels: `≥ 65 BUY · 40–64 CONSIDER · < 40 SKIP`. A BUY with no velocity or a
  zero quantity is shown as CONSIDER.

### Confidence Score (0–100 %) — independent, never multiplied

```
Confidence = 0.30·Mapping + 0.25·SalesSufficiency + 0.25·InventoryReliability + 0.20·ProfitabilityReliability
```

alias / unique-key → 100 · category+model fallback → 70 · active/active
duplicate → 40. `InventoryReliability` is 100 if the SKU joins to
`Inventar_Bestand`, else 0 (stock then = *unknown*, **never zero**).

### Quantity — periodic-review order-up-to-level

```
EffectiveStockPosition = Verfügbar + PurchasedToday + OlderIncoming
RequiredUnits          = ceil(DailyVelocity · TargetCoverageDays − EffectiveStockPosition)
RecommendedQuantity    = max(0, RequiredUnits) capped at PerSkuMaxExposure,
                         then rationed by the greedy daily-budget allocation (by GP per €).
```

`DailyVelocity = units(30d)/30` if `units(30d) ≥ 3`, else `units(90d)/90`, else
UNAVAILABLE (excluded from BUY).

---

## Configuration

`src/purchase_engine/config/engine.yml` is the shipped default. To customise:

```bash
cp src/purchase_engine/config/engine.yml ./engine.yml
$EDITOR ./engine.yml
python -m purchase_engine --config ./engine.yml --budget 1500
```

It is parsed into a frozen typed `EngineConfig` and **validated** every run; an
invalid file raises `ConfigError`. A short `config_hash` is stamped on every run
and asserted by the golden test.

---

## Development

| Task | Command |
|---|---|
| Lint | `make lint` (`ruff check`) |
| Auto-fix + format | `make format` |
| Type-check | `make typecheck` (`mypy`, strict on `src`) |
| Tests + coverage | `make test` (fails under 85 %) |
| Everything CI runs | `make check` |
| Sample run | `make run BUDGET=1500` |
| Regenerate golden | `make golden` |

CI (`.github/workflows/ci.yml`) runs Ruff, `ruff format --check`, mypy and the
test suite on Python 3.11 / 3.12 / 3.13. The golden test (`-m golden`) needs the
real workbook and is skipped in CI.

Toolchain: **Ruff** (lint + format), **mypy** (types), **pytest** +
**pytest-cov**, **pre-commit**, **hatchling** build backend, `src/` layout — the
2026 standard Python stack.

---

## Data assumptions & known limitations (from the plan)

- **`as_of`** defaults to the latest `Auftragsdatum` in `Tagesprofite`. If the
  sales feed is older than `data.stale_after_days`, the run still produces a list
  but stamps it `STALE_INPUTS`.
- **Incoming stock** is a rolling-window proxy off `EK_Normalisiert.Kaufdatum` —
  a *floor*, not a reconciled ledger. Bulk lots ("11× 3DS XL") count as one row.
  Flagged on every affected recommendation.
- **Returns / cancellations** carry no marker anywhere in the workbook — a
  returned sale still counts, so velocity and margin are an *upper bound*.
- **Profitability** is a trailing-window proxy, not the real Profit Engine; EK
  excludes refurbishment cost by design. Purchases above
  `profitability.ek_sanity_max_eur` are ignored (postal-code-as-price outliers).
- **Mapping**: most BUYs resolve via the category+model fallback, so mapping
  confidence caps near 70. No plausibility check on rows already marked `OK` yet.
- **No accuracy number** is claimed for the engine until the append-only history
  has been backtested against real outcomes.

## License

Proprietary — © 2026 BuyBack. See [LICENSE](LICENSE).
