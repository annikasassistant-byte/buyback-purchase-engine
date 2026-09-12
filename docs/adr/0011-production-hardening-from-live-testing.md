# 11. Production hardening found by testing the live Render deployment

Date: 2026-09-12

## Status

Accepted

## Context

Once the API was actually deployed to `buyback-purchase-engine-api.onrender.com`,
it was tested directly against that live instance and its real database -
not just `TestClient` against an in-process app - auth, CORS, injection
resistance, error handling, and a full `POST /runs` → `allocate` → `actions`
round trip. Four real findings came out of that pass, all fixed here.

## Decision

1. **`POST /runs` measures ~99s on Render's free tier**, not the ~17s
   measured locally (ADR 0010's number, from a dedicated dev machine against
   the same workbook). Free-tier CPU is shared and weaker - still nowhere
   near Render's 100-minute request limit, so the synchronous-handler
   decision in ADR 0010 stands, but the gap matters for what comes next.

2. **Added a process-local lock around `trigger_run`.** Two `POST /runs`
   arriving close together (a double-click before a "Run" button disables
   itself, or two buyers) would previously both run the full pipeline
   concurrently - on Render's shared free-tier CPU, competing for the same
   resource makes both *slower*, not parallel. The second request now gets
   `429 Too Many Requests` immediately instead of silently queueing behind
   the first at the CPU level. `threading.Lock`, not a distributed lock -
   correct for Render's default single instance; would need revisiting if
   this API is ever scaled to multiple instances.

3. **`/docs`, `/redoc`, `/openapi.json` are off by default once `API_KEY` is
   set.** They're FastAPI's own routes, added before `require_api_key`
   exists as a concept - they were never behind it, meaning the full endpoint
   list and request/response shapes were publicly browsable even though the
   data itself required a key. Not a data leak, but more surface than an
   internal tool needs exposed. Default is inferred (`api_key is None` -
   i.e. local dev only) with an explicit `ENABLE_DOCS` env var to override
   either direction.

4. **Two low-cost hardening fixes**, found by code review while investigating
   the above, not by an exploit against the live instance:
   - `require_api_key` compared the header with `!=`; switched to
     `secrets.compare_digest` (constant-time). Low-value target for a
     shared-secret header, but there was no reason to leave it timing-unsafe.
   - `CORSMiddleware` had `allow_credentials=True` with no cookie-based auth
     to justify it - the API only ever checks a header. Removed; revisit if
     Neon Auth session cookies get added to the frontend later. (Checked
     directly against the live deployment: the origin allow-list itself was
     never the problem - `CORS_ORIGINS` is a concrete list, not a wildcard,
     so this was unused permissiveness, not an active vulnerability.)

## Consequences

- A double-submitted "Run" click now surfaces as a clear `429` the frontend
  can show ("already running, hang on") instead of two runs quietly
  competing for the same CPU and both taking even longer.
- Local dev keeps interactive docs (no `API_KEY` set there by default); any
  real deployment doesn't, unless someone deliberately opts back in with
  `ENABLE_DOCS=true`.
- SQL-injection and malformed-body attempts were tested directly against the
  live instance; Render's own edge blocked the more obvious payloads before
  they reached the app at all (a "Blocked" page, not an app response) - a
  second layer on top of this codebase's actual defense, which is that every
  query in `adapters/store.py` / `adapters/query.py` is parameterized
  (`%s` placeholders), never string-built from request input.
- No change to the synchronous-request-handler decision (ADR 0010) - 99s is
  still comfortably inside Render's limits. If the workbook grows enough to
  push a run past a minute or two on a regular basis, that's the trigger to
  revisit a background-job design, not this finding on its own.
