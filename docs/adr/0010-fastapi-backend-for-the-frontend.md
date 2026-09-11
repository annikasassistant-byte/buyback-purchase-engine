# 10. This repo is the backend: FastAPI, synchronous, deployed on Render

Date: 2026-09-11

## Status

Accepted

## Context

The Phase-3 buyer UI is a separate repository (Next.js, deployed on Vercel).
It needs to actually **trigger the engine** - not only read a precomputed
list - so a buyer can click "Run" and get today's real BUY list, and move a
budget field and see it re-rank live. Two questions this ADR settles:

1. Where does the code that runs `Engine.run()` on demand live, and on what
   host?
2. What talks to Postgres - the frontend directly, or only this API?

On (1): Vercel is serverless-only - it cannot run a persistent Python
process. A Vercel Python *function* can run short bursts of Python, but
`Engine.run()` reads an xlsx with pandas/openpyxl and computes ~400 products;
that's a worse fit for a serverless function (cold starts, bundle-size limits,
ephemeral filesystem) than for a normal host that just... runs a process.

On (2): letting the frontend query Postgres directly (as an earlier draft of
this plan proposed) means distributing DB credentials into a public-facing
Next.js deployment and reimplementing query logic on both sides of the
language boundary. Routing everything through one API avoids both.

## Decision

- **This repository becomes the backend.** A new `purchase_engine.api`
  package (FastAPI) wraps the existing pipeline - it does not reimplement
  any scoring/allocation logic, it calls `Engine.run()` and
  `BudgetAllocator.allocate()` directly, same as `cli.py` does. It's another
  driving adapter at the same architectural tier as the CLI, not a new layer.
- **Deployed on Render**, not Vercel - a real, always-on Python host. The
  frontend (Vercel) talks to this API over HTTPS; it never holds a database
  credential or reimplements the allocation rule.
- **Synchronous request handlers.** Measured against the live API, not
  guessed: a full run over the shipped sample workbook (374 products) takes
  **~17 seconds**, almost all of it `openpyxl` reading the xlsx - still fine
  as a normal HTTP request on a host with no aggressive serverless timeout
  (Render's default is well above that), but real enough to design around,
  not "a few seconds". The live budget re-allocation endpoint, measured the
  same way, is **~0.5 seconds** - the ~34x gap is the entire point of ADR
  0009's flattened columns. No job queue for v1; revisit (return a job id
  immediately, poll for completion) if the workbook grows enough to push the
  full-run path past ~30s, or if Render's request timeout becomes the
  binding constraint - don't build that machinery pre-emptively.
- **Endpoints**, all under `/runs` and `/actions`:
  - `POST /runs` - run the real engine, persist via `PostgresStore`, return
    the run summary. What "click Run" calls.
  - `GET /runs/latest`, `GET /runs/{run_id}` - read a run summary.
  - `GET /runs/{run_id}/recommendations?label=BUY` - the product list.
  - `POST /runs/{run_id}/allocate` - live budget re-ranking (ADR 0009),
    reusing `BudgetAllocator` against rows reconstructed from Postgres
    (`adapters/query.py:load_buy_plans_for_allocation`) rather than a second,
    hand-rolled implementation of the same rule in TypeScript.
  - `POST /actions` / `GET /actions` - BUY/ADJUST/SKIP logging, the Phase-4
    backtest dataset.
- **Auth: a shared-secret `X-API-Key` header for v1**, not full OAuth. This
  API has exactly one legitimate caller today (the frontend), so a shared
  secret is proportionate; it's disabled (with a loud startup warning) when
  `API_KEY` is unset, which is only ever true in local dev. **Neon Auth is
  the named upgrade path** once the frontend needs real per-buyer identity
  (attributing a BUY/ADJUST/SKIP to a specific person, not just "the
  frontend") - that's a frontend-repo concern (sign-in UI) plus adding a
  verified user id to `buyer_action.actor` here; out of scope until the
  frontend actually has a login screen to drive it.
- **CORS** is allow-listed via `CORS_ORIGINS`, fails closed (empty by
  default in `.env.example`, `localhost:3000` for local dev) - a browser
  origin not on the list is refused, not silently allowed.

## Consequences

- Two hosts, two dashboards, two sets of env vars (Render + Vercel) - the
  cost of the frontend never touching Postgres directly. Judged worth it:
  credential isolation and one implementation of the allocation rule.
- `purchase_engine.api` depends on `adapters.query` (read side) in addition
  to `adapters.store` (write side, the engine's own port). The dependency
  still runs one way: nothing under `pipeline`/`domain` imports from `api` or
  `adapters.query`.
- No background-job infrastructure yet. If `POST /runs` ever needs to become
  async, the shape is already visible in the endpoint list above - `POST
  /runs` would return `202 Accepted` + a job id instead of `201` + the full
  summary; nothing else changes.
- The engine itself (`pipeline/`, `domain/`) is completely unaware this API
  exists - `cli.py` and `api/` are two peers wrapping the same core, exactly
  the point of the hexagonal boundary already in place.
