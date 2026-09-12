"""API runtime configuration - read once from the environment at process start.

Deliberately separate from ``purchase_engine.config.EngineConfig``: that one
is the *scoring* config (``engine.yml``, versioned, hashed into every run).
This one is *deployment* config (secrets, hosts) - it must never end up in
``config_hash`` or committed to the repo, so it stays env-var-only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from purchase_engine.adapters.store import dsn_from_env
from purchase_engine.errors import StoreError


@dataclass(frozen=True)
class ApiSettings:
    database_url: str
    api_key: str | None
    cors_origins: list[str]
    workbook_path: str | None
    enable_docs: bool

    @classmethod
    def from_env(cls) -> ApiSettings:
        # Pooled: the API makes many short-lived connections (one per
        # request), unlike the CLI's single long-lived one. See
        # `adapters.store.dsn_from_env`.
        dsn = dsn_from_env(pooled=True)
        if not dsn:
            msg = (
                "the API needs DATABASE_URL (or DATABASE_URL_POOLED) set - "
                "copy .env.example to .env and fill it in"
            )
            raise StoreError(msg)

        origins_raw = os.environ.get("CORS_ORIGINS", "http://localhost:3000")
        origins = [o.strip() for o in origins_raw.split(",") if o.strip()]
        api_key = os.environ.get("API_KEY") or None

        # /docs and /openapi.json aren't behind require_api_key (they're
        # FastAPI's own routes, not ours) - fine for local dev, not for a
        # deployed instance by default. Default: on with no API_KEY (dev),
        # off once one is set (deployed) - ENABLE_DOCS overrides either way.
        # See ADR 0011.
        enable_docs_raw = os.environ.get("ENABLE_DOCS")
        enable_docs = (
            enable_docs_raw.strip().lower() in ("1", "true", "yes")
            if enable_docs_raw is not None
            else api_key is None
        )

        return cls(
            database_url=dsn,
            api_key=api_key,
            cors_origins=origins,
            workbook_path=os.environ.get("WORKBOOK_PATH") or None,
            enable_docs=enable_docs,
        )


@lru_cache(maxsize=1)
def get_settings() -> ApiSettings:
    """Cached for the life of the process - env vars don't change mid-run."""
    return ApiSettings.from_env()
