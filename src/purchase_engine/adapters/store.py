"""Append-only recommendation history.

Annika pulled "run the engine continuously and store every recommendation" into
Phase 1, so persistence ships with the MVP - but zero-infra. :class:`FileStore`
writes three files under the artifacts directory:

* ``runs.jsonl`` - one line per engine run (metadata + counts), append-only.
* ``recommendations.jsonl`` - one line per ``(run_id, produkt_id)``, append-only.
  This is the dataset for the forward-looking backtest.
* ``latest.json`` - the full most-recent ``RecommendationSet`` (for the Phase-3 API).

A ``PostgresStore`` (``dim_product`` SCD2 + ``engine_run`` + ``recommendation``)
is the Phase-3 swap behind the same
:class:`~purchase_engine.domain.ports.RecommendationStore` port.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from purchase_engine.domain.models import RecommendationSet, to_jsonable


class NullStore:
    """Discards everything. Default when persistence is disabled."""

    def save(self, result: RecommendationSet) -> None:
        return None


class FileStore:
    """JSONL append-only history + a ``latest.json`` snapshot."""

    def __init__(self, artifacts_dir: str | Path) -> None:
        self.dir = Path(artifacts_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.runs_path = self.dir / "runs.jsonl"
        self.recs_path = self.dir / "recommendations.jsonl"
        self.latest_path = self.dir / "latest.json"

    def save(self, result: RecommendationSet) -> None:
        payload = to_jsonable(result)

        run_row = {k: v for k, v in payload.items() if k != "recommendations"}
        with self.runs_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(run_row, ensure_ascii=False) + "\n")

        with self.recs_path.open("a", encoding="utf-8") as fh:
            for rec in payload["recommendations"]:
                fh.write(
                    json.dumps(
                        {
                            "run_id": result.run_id,
                            "as_of": result.as_of,
                            "generated_at": result.generated_at,
                            **rec,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        self.latest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class SqliteStore:
    """Optional single-file mirror of the same history (``--sqlite``)."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self) -> None:
        with sqlite3.connect(self.path) as cx:
            cx.executescript(
                """
                CREATE TABLE IF NOT EXISTS engine_run (
                    run_id TEXT PRIMARY KEY, generated_at TEXT, as_of TEXT,
                    budget_eur REAL, config_hash TEXT, stale INTEGER,
                    counts_json TEXT, freshness_json TEXT
                );
                CREATE TABLE IF NOT EXISTS recommendation (
                    run_id TEXT, produkt_id TEXT, label TEXT,
                    purchase_score INTEGER, confidence INTEGER,
                    recommended_qty INTEGER, availability TEXT, payload_json TEXT,
                    PRIMARY KEY (run_id, produkt_id)
                );
                """
            )

    def save(self, result: RecommendationSet) -> None:
        payload = to_jsonable(result)
        with sqlite3.connect(self.path) as cx:
            cx.execute(
                "INSERT OR REPLACE INTO engine_run VALUES (?,?,?,?,?,?,?,?)",
                (
                    result.run_id,
                    result.generated_at,
                    result.as_of,
                    result.budget_eur,
                    result.config_hash,
                    int(result.data_freshness.stale),
                    json.dumps(payload["counts"], ensure_ascii=False),
                    json.dumps(payload["data_freshness"], ensure_ascii=False),
                ),
            )
            cx.executemany(
                "INSERT OR REPLACE INTO recommendation VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        result.run_id,
                        r["produkt_id"],
                        r["label"],
                        r["purchase_score"],
                        r["confidence"],
                        r["recommended_qty"],
                        r["availability"],
                        json.dumps(r, ensure_ascii=False),
                    )
                    for r in payload["recommendations"]
                ],
            )


class MultiStore:
    """Fan a single ``save`` out to several stores."""

    def __init__(self, *stores: object) -> None:
        self._stores = [s for s in stores if s is not None]

    def save(self, result: RecommendationSet) -> None:
        for store in self._stores:
            store.save(result)  # type: ignore[attr-defined]
