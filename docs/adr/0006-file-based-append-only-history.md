# 6. Persistence is file-based and append-only for the MVP

Date: 2026-09-03

## Status

Accepted

## Context

The agreed next step is outcome tracking: run the engine continuously, store
every recommendation, and later compare each day's recommendation with what
actually sold and got bought. That makes persistence a Phase-1 deliverable, not
a Phase-3 nicety.

The Technical Implementation Plan names PostgreSQL (`dim_product` SCD Type 2 +
`engine_run` + `recommendation`) as the target. But Postgres is infrastructure -
a server to host, secure, back up and give the engine credentials for - none of
which is needed to start accumulating the history, and none of which is Phase 2
scope (feature calc, scoring, quantity, explanation).

## Decision

- Persist behind a `RecommendationStore` port (`save(RecommendationSet)`).
- MVP adapter `FileStore` writes, under an artifacts directory:
  - `runs.jsonl` - one append-only line per run (metadata + counts);
  - `recommendations.jsonl` - one append-only line per `(run_id, produkt_id)`;
    this is the backtest dataset;
  - `latest.json` - the full most-recent set, for the Phase-3 API/UI.
- `SqliteStore` is an optional single-file mirror (`--sqlite`).
- `PostgresStore` is the Phase-3 swap behind the same port. The JSONL history is
  a superset of what it needs, so migration is a backfill script.

The `.jsonl` files are never rewritten - only appended - so the history is
immutable by construction.

## Consequences

- Zero infrastructure to run the engine on a schedule from day one.
- The append-only files are the point-in-time dataset the backtest needs; a
  spreadsheet could not retain this cleanly.
- SCD Type 2 for merged/deactivated products is **not** implemented yet; the MVP
  applies executed merges as a redirect on read (`build_merge_redirect`). Full
  SCD2 arrives with `PostgresStore`.
- Large `recommendations.jsonl` over months is fine for append + line-scan;
  querying it ad hoc is the operator's job until Postgres lands.
