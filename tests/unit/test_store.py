from __future__ import annotations

import json
import os
import sqlite3
import uuid

import psycopg
import pytest

from purchase_engine.adapters.store import (
    FileStore,
    MultiStore,
    NullStore,
    PostgresStore,
    SqliteStore,
    dsn_from_env,
)
from purchase_engine.domain.models import (
    ConfidenceBreakdown,
    DataFreshness,
    ProductFeatures,
    ProductProfitability,
    QuantityPlan,
    Recommendation,
    RecommendationSet,
    ScoreBreakdown,
)

_NEEDS_DB = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL (Neon) - not set here"
)


def _result(
    run_id: str = "r1", recommendations: list[Recommendation] | None = None
) -> RecommendationSet:
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
        recommendations=recommendations or [],
    )


def _recommendation(produkt_id: str = "BB999999") -> Recommendation:
    features = ProductFeatures(
        produkt_id=produkt_id,
        name="Test Product",
        kategorie="Handys",
        modell="TESTMODEL",
        is_duplicate=False,
        units_30d=5.0,
        units_90d=12.0,
        daily_velocity=0.167,
        velocity_window_days=30,
        days_since_sale=2,
        inventory_joined=True,
        current_sellable=0.0,
        on_hand=0.0,
        in_orders=0.0,
        purchased_today=0,
        older_incoming=1,
        effective_stock=1.0,
        days_of_supply=6.0,
        availability="OUT_OF_STOCK",
        join_source="produkt_id",
        mapping_quelle="unique_key",
        profitability=ProductProfitability(
            produkt_id=produkt_id,
            expected_vk=250.0,
            expected_ek=150.0,
            expected_gross_profit=100.0,
            margin_pct=0.4,
            status="CONFIRMED",
            source="trailing_window",
        ),
        margin_pct=0.4,
        hist_success=1.0,
        ok_rows=6,
    )
    score = ScoreBreakdown(
        demand=80.0,
        inventory_need=90.0,
        profit=70.0,
        market=None,
        overstock_penalty=0.0,
        effective_weights={"demand": 0.4, "inventory_need": 0.33, "profit": 0.27},
        single_component_capped=False,
        score=82,
    )
    confidence = ConfidenceBreakdown(
        mapping=100.0,
        sales_sufficiency=90.0,
        inventory_reliability=100.0,
        profitability_reliability=100.0,
        evidence_components_present=3,
        evidence_penalty=0.0,
        confidence=95,
    )
    quantity = QuantityPlan(
        daily_velocity=0.167,
        target_coverage_days=14.0,
        effective_stock=1.0,
        required_units=2,
        per_sku_capped_qty=2,
        recommended_qty=2,
        per_sku_cap=8,
        budget_trimmed=False,
    )
    return Recommendation(
        produkt_id=produkt_id,
        name="Test Product",
        kategorie="Handys",
        modell="TESTMODEL",
        label="BUY",
        purchase_score=82,
        confidence=95,
        recommended_qty=2,
        availability="OUT_OF_STOCK",
        features=features,
        score=score,
        confidence_breakdown=confidence,
        quantity=quantity,
        reasons=["Sold 5 units in the last 30 days."],
        risks=[],
        est_unit_ek=150.0,
        est_gross_profit_per_eur=0.667,
        est_total_cost=300.0,
        est_total_gross_profit=200.0,
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


@_NEEDS_DB
def test_postgres_store_upserts_and_flattens_budget_fields():
    dsn = dsn_from_env()
    assert dsn is not None
    # unique per invocation - CI's 3.11/3.12/3.13 matrix jobs run concurrently
    # against the same database; a fixed id here means one job's cleanup can
    # delete another job's still-in-progress row (see CHANGELOG).
    run_id = f"pytest-postgres-store-{uuid.uuid4().hex[:8]}"
    store = PostgresStore(dsn)
    try:
        rec = _recommendation()
        store.save(_result(run_id, [rec]))
        store.save(_result(run_id, [rec]))  # same run_id -> upsert, not duplicate

        with psycopg.connect(dsn) as cx, cx.cursor() as cur:
            cur.execute("select count(*) from engine_run where run_id = %s", (run_id,))
            run_count = cur.fetchone()
            assert run_count is not None
            assert run_count[0] == 1

            cur.execute(
                "select count(*), per_sku_capped_qty, est_unit_ek, est_gross_profit_per_eur "
                "from recommendation where run_id = %s "
                "group by per_sku_capped_qty, est_unit_ek, est_gross_profit_per_eur",
                (run_id,),
            )
            rows = cur.fetchall()
            assert len(rows) == 1  # one product, upserted not duplicated
            count, per_sku_capped_qty, est_unit_ek, est_gross_profit_per_eur = rows[0]
            assert count == 1
            assert per_sku_capped_qty == rec.quantity.per_sku_capped_qty
            assert est_unit_ek == rec.est_unit_ek
            assert est_gross_profit_per_eur == rec.est_gross_profit_per_eur
    finally:
        with psycopg.connect(dsn) as cx, cx.cursor() as cur:
            cur.execute("delete from engine_run where run_id = %s", (run_id,))  # cascades
