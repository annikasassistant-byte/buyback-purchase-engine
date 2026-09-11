# 9. PostgresStore (Neon) - shape driven by what the API actually reads

Date: 2026-09-11

## Status

Accepted. Amends [ADR 0006](0006-file-based-append-only-history.md)'s claim
that "full SCD2 arrives with `PostgresStore`" - see Consequences. Superseded
in part by [ADR 0010](0010-fastapi-backend-for-the-frontend.md), which adds
the API that actually reads this data.

## Context

The Phase-3 buyer UI needs two things from this repo that a local
`latest.json` can't give it:

1. A place to read the latest recommendation set from that isn't a file on a
   laptop's disk.
2. A way to answer "what would the BUY list look like at a different daily
   budget?" **live**, as the buyer types a number in - not only at the
   cadence the engine itself runs.

(2) matters because `budget_eur` is not a pipeline-wide input. Tracing it
through `pipeline/orchestrator.py` shows it reaches exactly one place:
`BudgetAllocator.allocate()` in `pipeline/quantity.py` - a pure, cheap,
pandas-free greedy rank-and-fill over data the rest of the pipeline has
already produced. Re-running the whole engine (workbook read, feature
calculation, scoring, confidence) for a budget change would be correct but
wasteful.

## Decision

- `PostgresStore` implements the existing `RecommendationStore` port
  (`save(RecommendationSet)`), same as `FileStore`/`SqliteStore`. Two tables,
  `engine_run` and `recommendation`, upserted per `(run_id, produkt_id)` so a
  re-run stays idempotent, matching the other two adapters. A third table,
  `buyer_action`, is written only by the API (§ADR 0010), never by this
  adapter.
- `recommendation` flattens exactly the fields a **live budget
  re-allocation** needs into real columns rather than leaving them inside a
  JSON blob: `per_sku_capped_qty` (the pre-budget quantity ceiling
  `BudgetAllocator` starts from), `est_unit_ek`, `est_gross_profit_per_eur`.
  Everything else (features, score/confidence breakdowns, reasons, risks)
  goes into a `payload JSONB` column - read for display, never needed by the
  allocation math itself.
- `DATABASE_URL` (direct connection) is what the CLI uses - one process, one
  connection per run. `DATABASE_URL_POOLED` (PgBouncer) is what the API uses -
  many short-lived connections, one per request. Both are read via
  `adapters.store.dsn_from_env(pooled=...)`.
- Loaded via `python-dotenv` if present (`.env`, gitignored); falls back to a
  real exported env var with no `.env` at all. `psycopg` and `python-dotenv`
  are both optional - the `postgres` extra - so the core CLI/tests don't gain
  a hard Postgres dependency just because this adapter exists.
- Schema creation (`ensure_schema`) is idempotent and exposed as a standalone
  function, not private to `PostgresStore.__init__`, because the API also
  needs it on startup (it writes `buyer_action` directly, never through
  `PostgresStore`).

## Consequences

- **Amends ADR 0006**: that ADR predicted "full SCD2 arrives with
  `PostgresStore`". It doesn't, on purpose - `dim_product` SCD2 (tracking
  product history/merges as first-class rows) is a real Phase-1 data-quality
  gap, but it's independent of what the API needs today. The MVP's
  merge-redirect-on-read (`build_merge_redirect`) stays exactly as it is;
  revisit SCD2 separately if that approach proves insufficient.
- `payload` JSONB duplicates data already in `runs.jsonl`/`recommendations.jsonl`
  - deliberate, not drift: Postgres is a second, queryable mirror of the same
  append-only history `FileStore` already guarantees, not its replacement.
  `FileStore` keeps running unconditionally; `--postgres` is additive, same
  pattern as `--sqlite`.
- A live budget change costs one indexed query + an in-memory sort over
  however many products are in the BUY tier (order-of-hundreds today) -
  milliseconds, not a full engine run.
