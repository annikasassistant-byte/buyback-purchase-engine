# BuyBack Purchase Engine

**Phase 2 MVP** — feature calculation · Purchase Score · Confidence Score ·
quantity + budget allocation · explanation generator.

A daily decision-support answer to one question: *what should we buy right now,
how much, and how sure are we?* It reads the existing product-matching parser's
**output** read-only and produces an explainable, ranked buy list. It never
re-parses a product name and never writes back to the parser
([ADR&nbsp;0002](docs/adr/0002-read-only-on-the-parser.md)).

Implements Phase 2 of the Technical Implementation Plan
(`docs/BuyBack Purchase Engine.html` in the analysis repo), plus the Postgres
store and HTTP API a Phase-3 buyer UI (separate repo) needs to actually run
this instead of reading a static file — see
[ADR&nbsp;0009](docs/adr/0009-postgres-store-for-the-frontend.md) /
[ADR&nbsp;0010](docs/adr/0010-fastapi-backend-for-the-frontend.md). Still out
of scope: the buyer UI itself (separate repo), the JTL API, Keepa / Back
Market signals, the real Profit Engine, a full
`PURCHASED→INCOMING→RECEIVED` ledger — each has a named seam.

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
make install          # editable install + dev tools + pre-commit hooks

python -m purchase_engine --budget 1500              # run against an auto-discovered workbook
```

`uv` works too: `uv sync --extra dev`.

The one input the MVP needs is the **`BuyBack - Profit` xlsx** (the parser's
offline export). It ships inside this repo, checked in as an **interim sample
dataset**, at `data/raw/full_dataset_2026_run/BuyBack - Profit (Aktualisiert
2026-09-02).xlsx` — a fresh clone already has it, no setup step needed. By
default the CLI searches upward from the working directory for that path, so
running from the repo root (or anywhere under it) just finds it; pass
`--workbook` to point at a different export instead. The Phase-1 inventory join
is already baked into that workbook as the `Inventar_Bestand` tab.

It's checked in *only* because there is no live connection yet (no Sheets API,
no JTL API — see "Out of scope" above); it's the one and only data source the
MVP has, not a general "data is fine to commit" policy. It carries real
private-seller data (name, postal code) and purchase prices, so `.gitignore`
allow-lists **only this exact file** — everything else dropped into `data/raw/`
(a fresh daily export, the live purchase table) stays ignored by default. See
[ADR&nbsp;0007](docs/adr/0007-ship-the-interim-sample-dataset.md) for the full
reasoning, including when this should be revisited.

```bash
python -m purchase_engine --workbook "/path/to/BuyBack - Profit ....xlsx" \
    --as-of 2026-08-24 --budget 1500
python -m purchase_engine --json > run.json          # machine-readable
python -m purchase_engine --sqlite                   # also mirror history to SQLite
python -m purchase_engine --postgres                 # also mirror to Postgres/Neon (needs DATABASE_URL)
purchase-engine --help                                # console script (after install)
```

Every run appends to `./artifacts/` (override with `--artifacts`):

| file | one row per | purpose |
|---|---|---|
| `runs.jsonl` | engine run | metadata, counts, data-freshness |
| `recommendations.jsonl` | `(run_id, produkt_id)` | **the backtest dataset** |
| `latest.json` | — | full most-recent `RecommendationSet` |

Both `.jsonl` files are append-only ([ADR&nbsp;0006](docs/adr/0006-file-based-append-only-history.md)).
`--postgres` mirrors the same history into Neon — see
[Backend API](#backend-api) below for the part that actually matters to a
frontend: Postgres, not these local files, is what the API reads.

---

## Repository layout

```
src/purchase_engine/
├── domain/            pure value objects + the ports the outer layers implement
│   ├── models.py        ProductFeatures, ScoreBreakdown, Recommendation, …
│   └── ports.py         Protocols: Profitability, IncomingStockSource, RecommendationStore
├── adapters/          the only layer that knows about Excel / files / SQL
│   ├── workbook.py      ParserWorkbook — read the profit xlsx, canonical columns
│   ├── incoming.py      EkNormalisiertIncoming (proxy) + LivePurchaseTableIncoming (stub)
│   ├── profitability.py TrailingWindowProfitability (the 6-field seam)
│   ├── store.py         FileStore / SqliteStore / PostgresStore / MultiStore / NullStore
│   └── query.py         Postgres read side for the API — latest run, live budget re-allocation
├── pipeline/          the computation; depends only on domain + config
│   ├── features.py      FeatureBuilder
│   ├── scoring.py       PurchaseScorer
│   ├── confidence.py    ConfidenceScorer
│   ├── quantity.py      QuantityPlanner + BudgetAllocator
│   ├── explain.py       ExplanationGenerator
│   └── orchestrator.py  Engine.run() — wires it together
├── api/               HTTP front door (FastAPI) — a driving adapter, peer of cli.py
│   ├── app.py            app factory, CORS, schema bootstrap on startup
│   ├── settings.py       env-var deployment config (DATABASE_URL, API_KEY, …)
│   ├── deps.py           X-API-Key auth dependency
│   ├── schemas.py        Pydantic request/response shapes
│   └── routers/          runs.py (trigger/read/allocate), actions.py (BUY/ADJUST/SKIP)
├── config.py         load + validate config/engine.yml -> typed EngineConfig
├── errors.py         PurchaseEngineError hierarchy
├── cli.py            argparse entry point (python -m purchase_engine)
└── config/engine.yml shipped default config

tests/   unit/  property/  golden/
docs/    architecture.md  adr/0001..0010
```

Imports point inward only: `domain ← adapters ← pipeline ← {cli, api}`. See
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
PurchaseScorer ──────────▶ ConfidenceScorer            QuantityPlanner
   0–100                     0–100 %                     order-up-to-level
   demand/stock/profit/      mapping/sales/inventory/    ceil(v·T − effective)
   market (redistributed)    profit + evidence breadth   capped per SKU
        └──────── never multiplied or combined ─┘                  │
        (Confidence reads which score components had data,   BudgetAllocator
         never the score's value - see ADR 0008)              (greedy GP/€)
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
           − EvidenceBreadthPenalty
```

alias / unique-key → 100 · category+model fallback → 70 · active/active
duplicate → 40. `InventoryReliability` is 100 if the SKU joins to
`Inventar_Bestand`, else 0 (stock then = *unknown*, **never zero**).

* **`EvidenceBreadthPenalty`** ([ADR&nbsp;0008](docs/adr/0008-confidence-reflects-purchase-score-evidence-breadth.md)):
  the four dimensions above each measure "how much do I trust *this one
  source*" — none of them measured "how many of the Purchase Score's own
  components actually had data for this product" until now. A product resting
  on a single surviving score component (the single-component-cap case above)
  docks Confidence by `confidence.evidence_breadth_penalty.one_component`
  (default 15); two of three present docks `...two_components` (default 5);
  all three → no penalty. Market is excluded from the count on purpose — it's
  `UNAVAILABLE` for every product in the MVP, not evidence about this one.

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

## Backend API

The frontend's only dependency — it never talks to Postgres directly, never
reimplements scoring/allocation logic. See
[ADR&nbsp;0010](docs/adr/0010-fastapi-backend-for-the-frontend.md).

```bash
pip install -e ".[api]"                # FastAPI + uvicorn + psycopg + dotenv
cp .env.example .env && $EDITOR .env   # DATABASE_URL_POOLED at minimum
make api-dev                            # http://localhost:8000, autoreload
# or: uvicorn purchase_engine.api.app:app --reload
```

Interactive docs at `/docs` once running (FastAPI's built-in Swagger UI).

| Endpoint | What it does |
|---|---|
| `POST /runs` | Runs the **real** engine (same code path as the CLI) and persists it. What a buyer's "Run" button calls. |
| `GET /runs/latest`, `GET /runs/{run_id}` | Read back a run's metadata/counts. |
| `GET /runs/{run_id}/recommendations?label=BUY` | The product list — score, confidence, reasons, risks. |
| `POST /runs/{run_id}/allocate` | Live budget re-ranking — re-runs only `BudgetAllocator` against cached data. No full engine run. What a budget field calls on every change. |
| `POST /actions`, `GET /actions` | Log/read BUY · ADJUST · SKIP — the Phase-4 backtest dataset. |

Auth is a shared secret: set `API_KEY`, the frontend sends it back as
`X-API-Key`. Unset in local dev only — every request is unauthenticated then,
with a startup warning saying so. `CORS_ORIGINS` is a comma-separated
allow-list (fails closed).

**Deployed on Render, not Vercel** — Vercel can't run a persistent Python
process; the engine's xlsx/pandas read is a bad fit for a serverless
function. `render.yaml` is a ready-to-use Blueprint: connect the repo in the
Render dashboard, set the two `sync: false` secrets
(`DATABASE_URL_POOLED`, `API_KEY`), deploy.

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
test suite on Python 3.11 / 3.12 / 3.13. The golden test (`-m golden`) is
explicitly excluded there (`-m "not golden"`) — CI checks out whatever has been
pushed to the remote, and this repository's sample dataset going in is a
separate decision from it being pushed (see
[ADR&nbsp;0007](docs/adr/0007-ship-the-interim-sample-dataset.md)). Locally,
with the sample dataset present, `make test` runs it like any other test.

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
