from __future__ import annotations

import json
import sqlite3

from purchase_engine.adapters.store import FileStore, MultiStore, NullStore, SqliteStore
from purchase_engine.domain.models import DataFreshness, RecommendationSet


def _result(run_id: str = "r1") -> RecommendationSet:
    return RecommendationSet(
        run_id=run_id,
        generated_at="2026-08-24T10:00:00",
        as_of="2026-08-24",
        budget_eur=1500.0,
        config_hash="abc123",
        data_freshness=DataFreshness(
            "2026-08-24", "2026-08-18", "2026-08-26", None, "2026-09-03", True, "n"
        ),
        counts={"scored": 0, "buy": 0},
        recommendations=[],
    )


def test_filestore_appends_and_writes_latest(tmp_path):
    store = FileStore(tmp_path)
    store.save(_result("r1"))
    store.save(_result("r2"))

    runs = (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["run_id"] for x in runs] == ["r1", "r2"]
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["run_id"] == "r2"
    assert "recommendations" not in json.loads(runs[0])  # run rows exclude the list


def test_sqlite_store_upserts(tmp_path):
    db = tmp_path / "h.sqlite"
    SqliteStore(db).save(_result("r1"))
    SqliteStore(db).save(_result("r1"))  # same run id -> replace, not duplicate
    with sqlite3.connect(db) as cx:
        assert cx.execute("select count(*) from engine_run").fetchone()[0] == 1


def test_multistore_fans_out(tmp_path):
    a, b = FileStore(tmp_path / "a"), FileStore(tmp_path / "b")
    MultiStore(a, b, None).save(_result())
    assert (tmp_path / "a" / "latest.json").exists()
    assert (tmp_path / "b" / "latest.json").exists()


def test_null_store_is_a_noop():
    NullStore().save(_result())
