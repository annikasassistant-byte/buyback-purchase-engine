"""FastAPI app factory. Run locally with:

::

    uvicorn purchase_engine.api.app:app --reload

or ``make api``. Deployed on Render - see ``render.yaml`` and
``docs/adr/0010-fastapi-backend-for-the-frontend.md`` for why not Vercel
(no persistent Python process there) and why synchronous request handlers
(a full run measures ~17s on a dedicated machine, ~99s on Render's free
tier's shared CPU - both fine against Render's 100-minute request limit,
see ADR 0011 for the concurrency guard that number justified).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from purchase_engine import __version__
from purchase_engine.adapters.store import ensure_schema
from purchase_engine.api.routers import actions, runs
from purchase_engine.api.settings import get_settings

log = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    ensure_schema(settings.database_url)  # idempotent - safe on every boot
    if settings.api_key is None:
        log.warning(
            "API_KEY is not set - every endpoint is unauthenticated. "
            "Fine for local dev against a scratch database only."
        )
    yield


def create_app() -> FastAPI:
    settings = get_settings()  # fail fast: no DATABASE_URL, no app
    app = FastAPI(
        title="BuyBack Purchase Engine API",
        version=__version__,
        description=(
            "HTTP front door onto the Purchase Engine: trigger a real engine "
            "run, read recommendations back, live budget re-allocation, and "
            "buyer-action logging. The frontend's only dependency - it never "
            "talks to Postgres directly."
        ),
        lifespan=_lifespan,
        # /docs, /redoc, /openapi.json are FastAPI's own routes - they don't
        # go through require_api_key. Fine to browse locally; off by default
        # anywhere API_KEY is set (ADR 0011). ENABLE_DOCS overrides either way.
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # No allow_credentials: auth is a header (X-API-Key), not a cookie -
        # credentialed CORS is unused and only widens the attack surface.
        # Revisit only if Neon Auth session cookies get added on the
        # frontend later (ADR 0011).
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(runs.router)
    app.include_router(actions.router)
    return app


app = create_app()
