from __future__ import annotations

import json
from typing import Any

import pytest

from purchase_engine import cli
from purchase_engine.domain.models import DataFreshness, RecommendationSet
from purchase_engine.errors import DataSourceError


def _fake_result() -> RecommendationSet:
    return RecommendationSet(
        run_id="run-x",
        generated_at="2026-08-24T10:00:00",
        as_of="2026-08-24",
        budget_eur=1500.0,
        config_hash="deadbeef0000",
        data_freshness=DataFreshness(
            "2026-08-24", "2026-08-18", "2026-08-26", None, "2026-09-03", True, "STALE"
        ),
        counts={
            "scored": 3,
            "buy": 1,
            "buy_funded": 1,
            "consider": 1,
            "skip": 1,
            "buy_actionable": 1,
            "inventory_joined": 2,
            "incoming_rows_today": 0,
            "incoming_rows_window": 0,
        },
        recommendations=[],
    )


@pytest.fixture
def fake_engine(monkeypatch, tmp_path):
    calls: dict[str, Any] = {}

    class _FakeEngine:
        def __init__(self, cfg, store=None):
            calls["store"] = store

        def run(self, workbook, as_of=None, budget_eur=None):
            calls["workbook"] = workbook
            calls["as_of"] = as_of
            calls["budget"] = budget_eur
            res = _fake_result()
            if store := calls["store"]:
                store.save(res)
            return res

    monkeypatch.setattr(cli, "Engine", _FakeEngine)
    monkeypatch.setattr(cli, "_find_default_workbook", lambda: tmp_path / "wb.xlsx")
    return calls


def test_cli_happy_path_writes_history(fake_engine, tmp_path, capsys):
    rc = cli.main(
        ["--artifacts", str(tmp_path / "art"), "--budget", "1500", "--as-of", "2026-08-24"]
    )
    assert rc == 0
    assert (tmp_path / "art" / "latest.json").exists()
    out = capsys.readouterr().out
    assert "run-x" in out and "STALE" in out and "== BUY" in out


def test_cli_json_mode(fake_engine, capsys):
    rc = cli.main(["--json", "--no-store"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == "run-x"


def test_cli_returns_2_when_no_workbook(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_find_default_workbook", lambda: None)
    assert cli.main(["--no-store"]) == 2


def test_cli_maps_engine_error_to_exit_1(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_find_default_workbook", lambda: tmp_path / "wb.xlsx")

    class _Boom:
        def __init__(self, *a, **k): ...
        def run(self, *a, **k):
            raise DataSourceError("no workbook")

    monkeypatch.setattr(cli, "Engine", _Boom)
    assert cli.main(["--no-store"]) == 1


def test_find_default_workbook_returns_none_when_absent(tmp_path):
    assert cli._find_default_workbook(tmp_path) is None
