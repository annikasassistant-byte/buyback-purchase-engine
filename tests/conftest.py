"""Shared fixtures."""

from __future__ import annotations

import pytest

from purchase_engine.config import EngineConfig, load_config
from purchase_engine.domain.models import ParserTables
from tests._factories import build_tables

try:
    # Load .env (if present) before collection, so the `DATABASE_URL`
    # skipif guards on the Postgres/API tests see it - matches the CLI's own
    # dotenv-if-present behaviour (adapters.store.dsn_from_env). A no-op in
    # CI: no `.env` there, nothing to load, those tests stay skipped exactly
    # as before.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv not installed
    pass


@pytest.fixture
def cfg() -> EngineConfig:
    return load_config()


@pytest.fixture
def make_tables():
    return build_tables


@pytest.fixture
def tables() -> ParserTables:
    return build_tables()
