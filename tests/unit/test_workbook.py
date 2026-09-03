from __future__ import annotations

from pathlib import Path

import pytest

from purchase_engine.adapters.workbook import (
    DEFAULT_WORKBOOK_GLOB,
    ParserWorkbook,
    find_default_workbook,
)
from purchase_engine.errors import DataSourceError


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_find_default_workbook_none_when_absent(tmp_path: Path):
    assert find_default_workbook(tmp_path) is None


def test_find_default_workbook_in_start_dir(tmp_path: Path):
    wb = _touch(tmp_path / "data" / "raw" / "full_dataset_2026_run" / "BuyBack - Profit (x).xlsx")
    assert find_default_workbook(tmp_path) == wb


def test_find_default_workbook_searches_parents(tmp_path: Path):
    wb = _touch(tmp_path / "data" / "raw" / "full_dataset_2026_run" / "BuyBack - Profit (x).xlsx")
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert find_default_workbook(deep) == wb


def test_find_default_workbook_picks_the_newest_by_name(tmp_path: Path):
    base = tmp_path / "data" / "raw" / "full_dataset_2026_run"
    _touch(base / "BuyBack - Profit (Aktualisiert 2026-08-24).xlsx")
    newest = _touch(base / "BuyBack - Profit (Aktualisiert 2026-09-02).xlsx")
    assert find_default_workbook(tmp_path) == newest


def test_default_glob_matches_the_expected_shape():
    assert DEFAULT_WORKBOOK_GLOB.endswith("BuyBack - Profit*.xlsx")
    assert "full_dataset_2026_run" in DEFAULT_WORKBOOK_GLOB


def test_parser_workbook_missing_file_raises_data_source_error(tmp_path: Path):
    with pytest.raises(DataSourceError, match="not found"):
        ParserWorkbook(tmp_path / "nope.xlsx")
