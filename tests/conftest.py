"""Shared fixtures."""

from __future__ import annotations

import pytest

from purchase_engine.config import EngineConfig, load_config
from purchase_engine.domain.models import ParserTables
from tests._factories import build_tables


@pytest.fixture
def cfg() -> EngineConfig:
    return load_config()


@pytest.fixture
def make_tables():
    return build_tables


@pytest.fixture
def tables() -> ParserTables:
    return build_tables()
