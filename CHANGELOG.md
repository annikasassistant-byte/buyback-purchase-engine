# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- **Hardening found by testing the live Render deployment** — see
  [ADR&nbsp;0011](docs/adr/0011-production-hardening-from-live-testing.md).
  - `POST /runs` now guarded by a process-local lock — measured ~99s per run
    on Render's free-tier shared CPU (vs ~17s on a dedicated dev machine);
    two overlapping runs previously competed for the same CPU instead of
    being independent. A second request while one is in flight now gets
    `429 Too Many Requests` immediately.
  - `/docs`, `/redoc`, `/openapi.json` are off by default once `API_KEY` is
    set — they're FastAPI's own routes and were never behind
    `require_api_key`. New `ENABLE_DOCS` env var to override either way.
  - `require_api_key` uses `secrets.compare_digest` (constant-time) instead
    of `!=`.
  - Removed unused `allow_credentials=True` from CORS config — auth is a
    header, not a cookie; nothing needed it.
  - `429` responses now carry a `Retry-After` header, estimated from how
    long the in-flight run has already taken. The lock moved from a bare
    module-level `threading.Lock` + `global` timestamp into a small
    `_RunGuard` class (`ruff` flagged the `global`; same behaviour either
    way).
  - Unconditional security-header middleware — `X-Content-Type-Options`,
    `X-Frame-Options`, `Referrer-Policy`, `Strict-Transport-Security`.
    Verified fixes actually reached production by redeploying and
    re-testing the live instance a second time (`/docs` → `404` confirmed
    it), not just locally.
  - New `GET /ready` — actually runs `SELECT 1` against Postgres, unlike
    `/health` (correctly a pure liveness check, left unchanged; still what
    Render's `healthCheckPath` watches). For diagnosing "is it the app or
    the database" without spending ~99s finding out via `POST /runs`.
  - Round 3 verification against the live instance: two **real** concurrent
    `POST /runs` fired at once (not mocked) — one `201` after 102s, one
    `429` after 0.7s, confirming the lock holds under real timing; confirmed
    no seller PII (name/postal code, present in the raw workbook per ADR
    0007) reaches any API response; confirmed unicode and embedded markup
    in a buyer note round-trip byte-for-byte and are never executed as HTML
    (this API renders nothing).
  - Round 4: `/ready` no longer returns the raw database exception text to
    an unauthenticated caller (OWASP API8:2023) — logged server-side,
    generic `"database unreachable"` in the response instead. Swept every
    other error-response site in `api/` for the same pattern; the one other
    place it looked similar (`POST /runs`'s `PurchaseEngineError` handling)
    is this codebase's own curated, deliberately user-facing exception
    vocabulary behind `X-API-Key` auth — reviewed and left as-is. Also
    confirmed connection behaviour under concurrent load directly against
    the live instance: 20 simultaneous reads, then 30 mixed across three
    endpoints, all `200`.

### Added

- **`PostgresStore` (Neon) + the `purchase_engine.api` backend** — see
  [ADR&nbsp;0009](docs/adr/0009-postgres-store-for-the-frontend.md) and
  [ADR&nbsp;0010](docs/adr/0010-fastapi-backend-for-the-frontend.md). The
  Phase-3 frontend's actual data source and trigger:
  - `PostgresStore` mirrors the same append-only history into Postgres
    (`engine_run` + `recommendation`, upserted per `(run_id, produkt_id)`),
    behind the `--postgres` CLI flag (needs `DATABASE_URL` — see
    `.env.example`). New `postgres` extra (`psycopg[binary]`,
    `python-dotenv`).
  - `purchase_engine.api` (new `api` extra: FastAPI + uvicorn) — a thin HTTP
    wrapper that runs the *real* engine on request, not a reimplementation:
    `POST /runs` triggers `Engine.run()`, `POST /runs/{id}/allocate` re-runs
    just `BudgetAllocator` for a new budget (live, no full re-run — the
    budget-only path traced through `pipeline/quantity.py`),
    `GET /runs/{id}/recommendations` and `POST/GET /actions` (buyer
    BUY/ADJUST/SKIP logging — the Phase-4 backtest dataset). Shared-secret
    `X-API-Key` auth, CORS allow-list. Deployed on Render — see
    `render.yaml`; Vercel can't host a persistent Python process.
  - `adapters/query.py` — the read side `PostgresStore` (write-only) doesn't
    have: latest/by-id run lookup, recommendation listing, and
    `load_buy_plans_for_allocation`, which reconstructs just enough of
    `QuantityPlan`/`ProductProfitability` from the flattened Postgres columns
    to feed the real `BudgetAllocator` — one implementation of the allocation
    rule, two callers (CLI and API).
  - New `buyer_action` table, written only by the API.

### Fixed

- Postgres-backed tests (`test_store.py`, `test_query.py`) used a fixed,
  hardcoded `run_id` — harmless single-job, but CI's 3.11/3.12/3.13 matrix
  runs concurrently against the same database, so one job's `finally:`
  cleanup could delete a row another job was still mid-assertion on. Caused
  `CI / quality (3.11)` and `(3.13)` to fail intermittently while `(3.12)`
  passed — not a real version difference, a race. Both now generate a
  `uuid.uuid4()`-suffixed `run_id` per test invocation.

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
