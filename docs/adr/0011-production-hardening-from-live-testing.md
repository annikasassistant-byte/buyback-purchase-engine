# 11. Production hardening found by testing the live Render deployment

Date: 2026-09-12

## Status

Accepted

## Context

Once the API was actually deployed to `buyback-purchase-engine-api.onrender.com`,
it was tested directly against that live instance and its real database -
not just `TestClient` against an in-process app - across three rounds, each
confirming the previous round's fixes actually reached production before
looking for more (`/docs` returning `404`, then the security headers
appearing, were the tells). Auth, CORS, injection resistance, error
handling, HTTP-method/case/trailing-slash behaviour, unicode round-tripping,
a PII check against the response payload, and a full `POST /runs` →
`allocate` → `actions` round trip - including, in round 3, two **real**
concurrent `POST /runs` fired at the live URL simultaneously (not mocked)
to prove the lock added in round 1 actually holds under real network
timing, not just in a unit test. Eight real findings total, all fixed here.

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
