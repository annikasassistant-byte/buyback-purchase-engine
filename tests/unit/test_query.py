from __future__ import annotations

import os

import pytest

from purchase_engine.adapters import query
from purchase_engine.adapters.store import PostgresStore, dsn_from_env

from .test_store import _recommendation, _result

_NEEDS_DB = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL (Neon) - not set here"
)


@pytest.fixture
def dsn() -> str:
    value = dsn_from_env()
    assert value is not None
    return value


@pytest.fixture
def seeded_run(dsn: str):
    """One run, one BUY-eligible product, cleaned up after the test."""
    run_id = "pytest-query-fixture"
    store = PostgresStore(dsn)
    rec = _recommendation("BB999998")
    store.save(_result(run_id, [rec]))
    try:
        yield run_id, rec
    finally:
        import psycopg

        with psycopg.connect(dsn) as cx, cx.cursor() as cur:
            cur.execute("delete from engine_run where run_id = %s", (run_id,))


@_NEEDS_DB
def test_get_run_and_latest_run_round_trip(dsn: str, seeded_run):
    run_id, _rec = seeded_run

    run = query.get_run(dsn, run_id)
    assert run is not None
    assert run["run_id"] == run_id
    assert run["budget_eur"] == 1500.0

    latest = query.get_latest_run(dsn)
    assert latest is not None  # some run exists - this fixture, at minimum

    assert query.get_run(dsn, "does-not-exist") is None


@_NEEDS_DB
def test_list_recommendations_filters_by_label(dsn: str, seeded_run):
    run_id, rec = seeded_run

    rows = query.list_recommendations(dsn, run_id)
    assert [r["produkt_id"] for r in rows] == [rec.produkt_id]
    assert rows[0]["payload"]["reasons"] == rec.reasons

    assert query.list_recommendations(dsn, run_id, label="SKIP") == []


@_NEEDS_DB
def test_load_buy_plans_for_allocation_reconstructs_inputs(dsn: str, seeded_run):
    run_id, rec = seeded_run

    inputs = query.load_buy_plans_for_allocation(dsn, run_id)

    assert rec.produkt_id in inputs.buy_plans
    plan = inputs.buy_plans[rec.produkt_id]
    assert plan.recommended_qty == rec.quantity.per_sku_capped_qty

    prof = inputs.profitability[rec.produkt_id]
    assert prof.expected_ek == rec.est_unit_ek
    # expected_gross_profit is derived back from gp_per_eur * unit_ek - not
    # stored directly, see the module docstring. Same relationship the real
    # BudgetAllocator derives it from, run in reverse.
    assert prof.expected_gross_profit == pytest.approx(
        rec.est_gross_profit_per_eur * rec.est_unit_ek
    )

    assert inputs.display[rec.produkt_id]["name"] == rec.name


@_NEEDS_DB
def test_buyer_action_insert_and_list(dsn: str, seeded_run):
    run_id, rec = seeded_run

    row = query.insert_buyer_action(
        dsn, run_id=run_id, produkt_id=rec.produkt_id, action="BUY", qty=2, actor="pytest"
    )
    assert row["action"] == "BUY"
    assert row["qty"] == 2

    actions = query.list_buyer_actions(dsn, run_id=run_id)
    assert len(actions) == 1
    assert actions[0]["id"] == row["id"]
