# 11. Production hardening found by testing the live Render deployment

Date: 2026-09-12

## Status

Accepted

## Context

Once the API was actually deployed to `buyback-purchase-engine-api.onrender.com`,
it was tested directly against that live instance and its real database -
not just `TestClient` against an in-process app - across four rounds, each
confirming the previous round's fixes actually reached production before
looking for more (`/docs` returning `404`, then the security headers
appearing, then `/ready` responding, were the tells). Auth, CORS, injection
resistance, error handling, HTTP-method/case/trailing-slash behaviour,
unicode round-tripping, a PII check against the response payload, connection
behaviour under concurrent load, and a full `POST /runs` → `allocate` →
`actions` round trip - including, in round 3, two **real** concurrent
`POST /runs` fired at the live URL simultaneously (not mocked) to prove the
lock added in round 1 actually holds under real network timing, not just in
a unit test. Nine real findings total, all fixed here.

Round 5 changed *kind*, not just count: four rounds of manual dynamic testing
(hand-crafted requests against live endpoints) had converged - round 4 took
real digging to find one genuine issue, against three or four each in earlier
rounds, the expected shape of a maturing pass, not a sign of nothing left to
check. Continuing to hand-poke endpoints past that point would have been
motion, not progress. The two credible next steps named at the end of round
4 were automated security tooling or waiting for the frontend to exist;
round 5 is the first of those - `pip-audit` (dependency CVEs) and `bandit`
(static analysis), run for the first time.

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

5. **`429`s now carry a `Retry-After` header.** RFC 9110 expects one; without
   it a frontend has no signal for how long to wait before showing "try
   again". `_RunGuard.retry_after_seconds()` estimates it from how long the
   in-flight run has already taken versus the ~99s worst case measured above
   - a hint, not an exact promise, but better than nothing. (The lock itself
   moved from a bare module-level `threading.Lock` + a `global`-mutated
   timestamp into a small `_RunGuard` class - `ruff` correctly flagged the
   `global` statement as a smell; the class version needs neither `global`
   nor any behaviour change.)

6. **Added unconditional security-header middleware** - `X-Content-Type-Options:
   nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
   `Strict-Transport-Security`. None of these change behaviour for a pure
   JSON API with no HTML views and no cookies; they cost nothing and close
   off a class of findings any future scan would otherwise flag. HSTS is
   safe unconditionally here specifically because Render terminates TLS in
   front of this app - plain HTTP never reaches it.

7. **Added `GET /ready`, separate from `GET /health`.** `/health` is
   correctly a pure liveness check per current guidance (no I/O - if it's
   down, restarting the process is the right call) and stays wired to
   Render's `healthCheckPath` unchanged. `/ready` actually runs `SELECT 1`
   against Postgres - not for Render's own restart logic (a transient Neon
   blip making Render cycle the whole service would fix nothing), but so a
   human - or the frontend - can tell "is it the app or the database" during
   an incident without spending ~99s finding out the hard way via `POST /runs`.

**Verified, not just fixed, against the live instance in round 3:**
- **The concurrency lock, for real.** Fired two genuine `POST /runs` at the
  live URL at once: one came back `201` after 102s, the other `429` after
  0.7s, and the loser's response confirmed it never touched the database -
  the mocked local test (fast, cheap) proves the route wiring; this proved
  the actual behavior under real network/CPU timing.
- **No PII leak.** The workbook carries real seller names and postal codes
  (ADR 0007); checked a full recommendation payload field-by-field against
  the live API - none of it appears. `ProductFeatures`/`Recommendation` are
  aggregated per product, not per purchase transaction, so the seller PII
  living in `EK_Normalisiert` never reaches this layer. Worth confirming
  explicitly rather than assuming.
- **Unicode and embedded markup round-trip correctly.** A buyer note with
  German umlauts, an emoji, and a literal `<script>` tag came back
  byte-for-byte identical on read-back. (An earlier attempt to test this
  produced a `400` - traced to this session's own shell mangling the
  UTF-8 in a `curl -d` argument, not an API bug; re-tested via a file body
  to confirm.) The `<script>` tag is stored as inert string data - this API
  never renders HTML, so there's nothing here for it to execute against;
  that responsibility sits with whatever eventually displays a note.

**Round 4 - one more real finding, plus a load check:**

8. **`/ready` leaked the raw database exception to an unauthenticated
   caller.** It's unauthenticated on purpose, same convention as `/health`
   (readiness probes are conventionally public) - but that's exactly why a
   raw `psycopg` exception (can include the DB host) had no business in the
   response body just because Postgres was briefly unreachable. OWASP
   API8:2023 (Security Misconfiguration) names this pattern directly. Fixed:
   the detail goes to `log.error` server-side; the client gets a fixed
   `"database unreachable"` string. Swept every other `HTTPException` raise
   site in `api/` for the same pattern - the one other place a caught
   exception's text reaches a response (`POST /runs`'s `PurchaseEngineError`
   handling) is different in kind, not just degree: those are this
   codebase's own curated, deliberately user-facing exception classes (see
   `errors.py`'s own docstring), not a raw driver exception, and that
   endpoint requires `X-API-Key` - reviewed and left as-is.
9. **Connection behaviour under concurrent load, checked directly against
   the live instance** - 20 simultaneous `GET /runs/latest`, then 30 mixed
   requests across three endpoints (`/recommendations`, `/ready`,
   `/runs/latest`) at once. All 200s. `DATABASE_URL_POOLED` (PgBouncer) is
   exactly what this was chosen for in ADR 0009 - confirmed, not just
   assumed.

**Round 5 - automated tooling, not more manual requests:**

10. **`bandit` static-analysis sweep of the whole package** (`src/purchase_engine`,
    not just `api/`) found one real, if minor, thing: `adapters/query.py`
    used a bare `assert` to guard "`INSERT ... RETURNING` always yields a
    row" (CWE-703 / B101) - `assert` statements are silently stripped when
    Python runs with `-O`, which would turn a should-never-happen case into
    an actual `AttributeError` on a `None` a few lines later instead of a
    clear error. Replaced with an explicit `if row is None: raise StoreError`.
    Full re-scan after the fix: zero findings across 3,190 lines.
11. **`pip-audit` against exactly what Render deploys - not this dev
    machine's shared venv.** The first run (against the ambient dev
    environment) reported 82 "vulnerabilities" - all in packages like
    `gitpython`, `pypdf`, `soupsieve` that belong to *other, unrelated*
    projects sharing this machine's Python install, not to this API at all;
    reporting those as findings would have been wrong. Re-ran properly: a
    throwaway venv with only `pip install -e ".[api]"` - the exact command
    `render.yaml`'s `buildCommand` runs - then audited *that*. Zero known
    vulnerabilities in the real, deployed dependency set (`fastapi` 0.141.1,
    `psycopg` 3.3.5, `uvicorn` 0.52.4, `starlette` 1.6.0, `pydantic` 2.13.5,
    and the rest).

**Considered and deliberately not done**: a full rate-limiting middleware
(e.g. `slowapi` + Redis) for the general endpoint surface. This API has
exactly one legitimate caller (the frontend, behind the shared `API_KEY`),
and the one endpoint with a real abuse/cost profile (`POST /runs`, ~99s of
CPU and real Neon compute per call) already has the concurrency guard above.
Adding a request-counting rate limiter on top, with the extra dependency and
(for it to work correctly across restarts) a Redis backend, would be
defending against a threat model this deployment doesn't actually have yet.
Revisit if the API ever gets more than one legitimate caller, or moves
off a single shared secret.

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
- `bandit` and `pip-audit` aren't one-off round-5 commands - both are now in
  the `dev`/`api` extras, `make bandit` / `make audit` / `make security` run
  them locally, and CI runs both once per push (gated to the 3.12 matrix
  leg - neither tool's result depends on the Python version). A future
  dependency bump or new code path that introduces a real finding gets
  caught automatically, not only when someone remembers to ask for another
  round of manual testing.
