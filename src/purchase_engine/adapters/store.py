"""Append-only recommendation history.

Annika pulled "run the engine continuously and store every recommendation" into
Phase 1, so persistence ships with the MVP - but zero-infra. :class:`FileStore`
writes three files under the artifacts directory:

* ``runs.jsonl`` - one line per engine run (metadata + counts), append-only.
* ``recommendations.jsonl`` - one line per ``(run_id, produkt_id)``, append-only.
  This is the dataset for the forward-looking backtest.
* ``latest.json`` - the full most-recent ``RecommendationSet`` (for the Phase-3 API).

:class:`PostgresStore` mirrors the same history into Postgres (Neon) - the
backend API's actual data source (``purchase_engine.api``), not just a local
artefact. See :doc:`/docs/adr/0009-postgres-store-for-the-frontend` for why the
schema flattens the budget-relevant fields instead of a full ``dim_product``
SCD2 design. Optional - needs the ``postgres`` extra
(``pip install -e ".[postgres]"``), which brings ``psycopg`` and
``python-dotenv``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from purchase_engine.domain.models import RecommendationSet, to_jsonable
from purchase_engine.errors import StoreError

try:
    import psycopg
    from psycopg.types.json import Json
except ImportError:  # pragma: no cover - exercised only without the 'postgres' extra
    psycopg = None  # type: ignore[assignment]
    Json = None  # type: ignore[assignment, misc]

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - exercised only without the 'postgres' extra
    load_dotenv = None  # type: ignore[assignment]


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


def dsn_from_env(*, pooled: bool = False) -> str | None:
    """Read a Neon connection string from the environment.

    Loads ``.env`` first via ``python-dotenv`` if installed and present - a
    silent no-op otherwise, so this still works with a real exported env var
    and no ``.env`` at all.

    ``pooled=False`` (default) returns ``DATABASE_URL`` - the direct
    connection, right for a single long-lived process making one connection
    per run (the CLI: ``python -m purchase_engine --postgres``).

    ``pooled=True`` returns ``DATABASE_URL_POOLED`` (PgBouncer), right for
    many short-lived connections (the API, one request = one connection),
    falling back to the direct URL if no pooled one is configured.
    """
    if load_dotenv is not None:
        load_dotenv()
    if pooled:
        return os.environ.get("DATABASE_URL_POOLED") or os.environ.get("DATABASE_URL")
    return os.environ.get("DATABASE_URL")


_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_run (
    run_id        TEXT PRIMARY KEY,
    generated_at  TIMESTAMPTZ NOT NULL,
    as_of         DATE NOT NULL,
    budget_eur    DOUBLE PRECISION,
    config_hash   TEXT NOT NULL,
    stale         BOOLEAN NOT NULL,
    counts        JSONB NOT NULL,
    freshness     JSONB NOT NULL,
    inserted_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS recommendation (
    run_id                    TEXT NOT NULL REFERENCES engine_run (run_id) ON DELETE CASCADE,
    produkt_id                TEXT NOT NULL,
    name                      TEXT NOT NULL,
    kategorie                 TEXT NOT NULL,
    label                     TEXT NOT NULL,
    purchase_score            INTEGER NOT NULL,
    confidence                INTEGER NOT NULL,
    availability              TEXT NOT NULL,
    -- pre-budget quantity ceiling: what BudgetAllocator saw before rationing.
    -- the one number a *live* budget re-allocation needs per product.
    per_sku_capped_qty        INTEGER NOT NULL,
    -- post-allocation outcome, specific to the budget *this* run used
    recommended_qty           INTEGER NOT NULL,
    budget_trimmed            BOOLEAN NOT NULL,
    est_unit_ek               DOUBLE PRECISION,
    est_gross_profit_per_eur  DOUBLE PRECISION,
    est_total_cost            DOUBLE PRECISION,
    est_total_gross_profit    DOUBLE PRECISION,
    -- everything else - features / score & confidence breakdown / reasons / risks
    payload                   JSONB NOT NULL,
    PRIMARY KEY (run_id, produkt_id)
);

CREATE INDEX IF NOT EXISTS recommendation_produkt_id_idx ON recommendation (produkt_id);
CREATE INDEX IF NOT EXISTS recommendation_run_label_idx ON recommendation (run_id, label);

-- BUY / ADJUST / SKIP, logged from the buyer UI - the Phase-4 backtest dataset.
CREATE TABLE IF NOT EXISTS buyer_action (
    id          BIGSERIAL PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES engine_run (run_id) ON DELETE CASCADE,
    produkt_id  TEXT NOT NULL,
    action      TEXT NOT NULL CHECK (action IN ('BUY', 'ADJUST', 'SKIP')),
    qty         INTEGER,
    note        TEXT,
    actor       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS buyer_action_run_product_idx ON buyer_action (run_id, produkt_id);
"""


def ensure_schema(dsn: str) -> None:
    """Create the Postgres tables/indexes if they don't exist yet. Idempotent -
    safe to call on every process start (:class:`PostgresStore` does, and so
    does the API on startup, since it writes ``buyer_action`` without ever
    going through :class:`PostgresStore`)."""
    if psycopg is None:  # pragma: no cover
        # mypy runs in this project's dev env, which always has the
        # 'postgres' extra - so it (correctly, for *that* env) sees psycopg
        # as never None and calls this line unreachable. It isn't dead code:
        # this module is also importable from a plain `pip install -e .`
        # with no extras, where the top-of-file import silently fails instead.
        msg = "Postgres support needs the 'postgres' extra: pip install -e '.[postgres]'"  # type: ignore[unreachable]
        raise StoreError(msg)
    with psycopg.connect(dsn) as cx, cx.cursor() as cur:
        cur.execute(_POSTGRES_SCHEMA)


class PostgresStore:
    """Mirrors the same append-only history into Postgres (Neon).

    Upserted per ``(run_id, produkt_id)`` so re-running the engine over the
    same inputs stays idempotent, exactly like :class:`FileStore` /
    :class:`SqliteStore`. The budget-relevant fields
    (``per_sku_capped_qty``, ``est_unit_ek``, ``est_gross_profit_per_eur``)
    are flattened into real columns rather than left inside ``payload`` -
    that's deliberate, see the module docstring: it's what the API's live
    budget re-allocation endpoint queries directly instead of round-tripping
    the whole engine.
    """

    def __init__(self, dsn: str) -> None:
        if psycopg is None:  # pragma: no cover - exercised only without the extra
            msg = "PostgresStore needs the 'postgres' extra: pip install -e '.[postgres]'"  # type: ignore[unreachable]
            raise StoreError(msg)
        self.dsn = dsn
        ensure_schema(dsn)

    def save(self, result: RecommendationSet) -> None:
        payload = to_jsonable(result)
        with psycopg.connect(self.dsn) as cx, cx.cursor() as cur:
            cur.execute(
                """
                INSERT INTO engine_run
                    (run_id, generated_at, as_of, budget_eur, config_hash, stale,
                     counts, freshness)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    generated_at = EXCLUDED.generated_at,
                    as_of        = EXCLUDED.as_of,
                    budget_eur   = EXCLUDED.budget_eur,
                    config_hash  = EXCLUDED.config_hash,
                    stale        = EXCLUDED.stale,
                    counts       = EXCLUDED.counts,
                    freshness    = EXCLUDED.freshness
                """,
                (
                    result.run_id,
                    result.generated_at,
                    result.as_of,
                    result.budget_eur,
                    result.config_hash,
                    result.data_freshness.stale,
                    Json(payload["counts"]),
                    Json(payload["data_freshness"]),
                ),
            )
            cur.executemany(
                """
                INSERT INTO recommendation
                    (run_id, produkt_id, name, kategorie, label, purchase_score,
                     confidence, availability, per_sku_capped_qty, recommended_qty,
                     budget_trimmed, est_unit_ek, est_gross_profit_per_eur,
                     est_total_cost, est_total_gross_profit, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, produkt_id) DO UPDATE SET
                    label                    = EXCLUDED.label,
                    purchase_score           = EXCLUDED.purchase_score,
                    confidence               = EXCLUDED.confidence,
                    availability             = EXCLUDED.availability,
                    per_sku_capped_qty       = EXCLUDED.per_sku_capped_qty,
                    recommended_qty          = EXCLUDED.recommended_qty,
                    budget_trimmed           = EXCLUDED.budget_trimmed,
                    est_unit_ek              = EXCLUDED.est_unit_ek,
                    est_gross_profit_per_eur = EXCLUDED.est_gross_profit_per_eur,
                    est_total_cost           = EXCLUDED.est_total_cost,
                    est_total_gross_profit   = EXCLUDED.est_total_gross_profit,
                    payload                  = EXCLUDED.payload
                """,
                [self._recommendation_row(result.run_id, r) for r in payload["recommendations"]],
            )

    @staticmethod
    def _recommendation_row(run_id: str, r: dict[str, Any]) -> tuple[Any, ...]:
        return (
            run_id,
            r["produkt_id"],
            r["name"],
            r["kategorie"],
            r["label"],
            r["purchase_score"],
            r["confidence"],
            r["availability"],
            r["quantity"]["per_sku_capped_qty"],
            r["recommended_qty"],
            r["quantity"]["budget_trimmed"],
            r["est_unit_ek"],
            r["est_gross_profit_per_eur"],
            r["est_total_cost"],
            r["est_total_gross_profit"],
            Json(r),
        )


class MultiStore:
    """Fan a single ``save`` out to several stores."""

    def __init__(self, *stores: object) -> None:
        self._stores = [s for s in stores if s is not None]

    def save(self, result: RecommendationSet) -> None:
        for store in self._stores:
            store.save(result)  # type: ignore[attr-defined]
