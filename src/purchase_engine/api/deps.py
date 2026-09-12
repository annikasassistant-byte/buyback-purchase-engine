"""FastAPI dependencies - auth, mostly. Settings live in ``api.settings``."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from purchase_engine.api.settings import get_settings


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    """Shared-secret auth: the frontend sends ``X-API-Key``, checked against
    ``API_KEY``. Disabled with no ``API_KEY`` set - convenient for local dev
    against a scratch database, never set that way anywhere deployed. See
    ``docs/adr/0010-fastapi-backend-for-the-frontend.md`` for why this and
    not full OAuth for v1 (Neon Auth is the named upgrade path once the
    frontend needs real buyer identity, not just "is this our frontend").

    Uses ``secrets.compare_digest`` rather than ``!=`` - a plain string
    comparison returns as soon as it finds a mismatched character, so the
    response time leaks how many leading characters of a guess were right.
    Low-value target for a shared-secret header, but the constant-time
    comparison costs nothing to use correctly (ADR 0011).
    """
    settings = get_settings()
    if settings.api_key is None:
        return
    if x_api_key is None or not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing X-API-Key")
