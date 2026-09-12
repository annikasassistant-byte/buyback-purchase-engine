"""Read-side Postgres queries for the API.

:class:`~purchase_engine.adapters.store.PostgresStore` is the write side (the
engine's ``RecommendationStore`` port). This module is the read side the API
needs and the engine never does - "what's the latest run", "re-allocate this
run's BUY list at a new budget", "log a buyer's decision". Kept separate from
``store.py`` on purpose: the engine's own pipeline never imports this module,
only ``purchase_engine.api`` does - the port/adapter boundary stays one-way.

``load_buy_plans_for_allocation`` reconstructs just enough of
:class:`~purchase_engine.domain.models.QuantityPlan` and
:class:`~purchase_engine.domain.models.ProductProfitability` from the flattened
columns :class:`~purchase_engine.adapters.store.PostgresStore` wrote to feed
back into the *real* :class:`~purchase_engine.pipeline.quantity.BudgetAllocator`
- see :doc:`/docs/adr/0010-fastapi-backend-for-the-frontend` for why that's a
reconstruction rather than a second, hand-rolled allocation implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from purchase_engine.domain.models import ProductProfitability, QuantityPlan
from purchase_engine.errors import StoreError

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - exercised only without the 'postgres' extra
    psycopg = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]


def _require_psycopg() -> None:
    # See the matching comment in adapters/store.py:ensure_schema - mypy runs
    # where the 'postgres' extra is always installed, so it correctly (for
    # that env) treats this guard as dead code. It isn't, for an install
    # without the extra.
    if psycopg is None:  # pragma: no cover
        msg = "Postgres queries need the 'postgres' extra: pip install -e '.[postgres]'"  # type: ignore[unreachable]
        raise StoreError(msg)


def get_latest_run(dsn: str) -> dict[str, Any] | None:
    """The most recently generated ``engine_run`` row, or ``None`` if empty."""
    _require_psycopg()
    with psycopg.connect(dsn, row_factory=dict_row) as cx, cx.cursor() as cur:
        cur.execute("SELECT * FROM engine_run ORDER BY generated_at DESC LIMIT 1")
        return cur.fetchone()


def get_run(dsn: str, run_id: str) -> dict[str, Any] | None:
    """One ``engine_run`` row by id, or ``None`` if it doesn't exist."""
    _require_psycopg()
    with psycopg.connect(dsn, row_factory=dict_row) as cx, cx.cursor() as cur:
        cur.execute("SELECT * FROM engine_run WHERE run_id = %s", (run_id,))
        return cur.fetchone()


def list_recommendations(
    dsn: str, run_id: str, *, label: str | None = None
) -> list[dict[str, Any]]:
    """Recommendation rows for a run, each with its full ``payload`` merged in -
    exactly what a buyer-facing list view renders (score/confidence/reasons/
    risks), ordered the same way the CLI report is: BUY first, funded first,
    then by GP/EUR, then by score."""
    _require_psycopg()
    sql = "SELECT * FROM recommendation WHERE run_id = %s"
    params: tuple[Any, ...] = (run_id,)
    if label is not None:
        sql += " AND label = %s"
        params = (run_id, label)
    with psycopg.connect(dsn, row_factory=dict_row) as cx, cx.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    order = {"BUY": 0, "CONSIDER": 1, "SKIP": 2}
    rows.sort(
        key=lambda r: (
            order.get(r["label"], 3),
            0 if (r["label"] == "BUY" and r["recommended_qty"] > 0) else 1,
            -(r["est_gross_profit_per_eur"] or -1.0),
            -r["purchase_score"],
            r["produkt_id"],
        )
    )
    return rows


@dataclass
class AllocationInput:
    """Just enough of a run's BUY tier to re-run
    :class:`~purchase_engine.pipeline.quantity.BudgetAllocator` for a new
    budget - the reconstruction ``load_buy_plans_for_allocation`` builds."""

    buy_plans: dict[str, QuantityPlan]
    profitability: dict[str, ProductProfitability]
    # for the response: display fields not needed by allocate() itself
    display: dict[str, dict[str, Any]]


def load_buy_plans_for_allocation(dsn: str, run_id: str) -> AllocationInput:
    """Reconstruct the inputs ``BudgetAllocator.allocate()`` needs, from the
    columns :class:`~purchase_engine.adapters.store.PostgresStore` flattened -
    BUY-labelled rows with a non-zero pre-budget quantity ceiling only, same
    filter the orchestrator applies before calling the real allocator.

    ``expected_gross_profit`` (a per-unit EUR amount) isn't stored directly -
    only ``est_gross_profit_per_eur`` (a ratio) is. It's recovered exactly as
    ``gp_per_eur * unit_ek``, the same relationship
    ``pipeline/quantity.py:BudgetAllocator.allocate`` derives it from in the
    first place - not an approximation, just run in reverse.
    """
    _require_psycopg()
    with psycopg.connect(dsn, row_factory=dict_row) as cx, cx.cursor() as cur:
        cur.execute(
            """
            SELECT produkt_id, name, kategorie, purchase_score, confidence,
                   availability, per_sku_capped_qty, est_unit_ek, est_gross_profit_per_eur
            FROM recommendation
            WHERE run_id = %s AND label = 'BUY' AND per_sku_capped_qty > 0
            """,
            (run_id,),
        )
        rows = cur.fetchall()

    buy_plans: dict[str, QuantityPlan] = {}
    profitability: dict[str, ProductProfitability] = {}
    display: dict[str, dict[str, Any]] = {}
    for row in rows:
        pid = row["produkt_id"]
        capped = row["per_sku_capped_qty"]
        unit_ek = row["est_unit_ek"]
        gp_per_eur = row["est_gross_profit_per_eur"]
        expected_gross_profit = gp_per_eur * unit_ek if gp_per_eur is not None and unit_ek else None

        # Only the fields BudgetAllocator.allocate() actually reads are real;
        # the rest hold placeholder values to satisfy the dataclass shape -
        # this object is never persisted or shown, only fed into allocate().
        buy_plans[pid] = QuantityPlan(
            daily_velocity=None,
            target_coverage_days=0.0,
            effective_stock=0.0,
            required_units=None,
            per_sku_capped_qty=capped,
            recommended_qty=capped,
            per_sku_cap=capped,
            budget_trimmed=False,
        )
        profitability[pid] = ProductProfitability(
            produkt_id=pid,
            expected_vk=None,
            expected_ek=unit_ek,
            expected_gross_profit=expected_gross_profit,
            margin_pct=None,
            status="CONFIRMED",
            source="postgres_replay",
        )
        display[pid] = {
            "name": row["name"],
            "kategorie": row["kategorie"],
            "purchase_score": row["purchase_score"],
            "confidence": row["confidence"],
            "availability": row["availability"],
        }
    return AllocationInput(buy_plans=buy_plans, profitability=profitability, display=display)


def insert_buyer_action(
    dsn: str,
    *,
    run_id: str,
    produkt_id: str,
    action: str,
    qty: int | None = None,
    note: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Log one BUY/ADJUST/SKIP decision. Returns the inserted row."""
    _require_psycopg()
    with psycopg.connect(dsn, row_factory=dict_row) as cx, cx.cursor() as cur:
        cur.execute(
            """
            INSERT INTO buyer_action (run_id, produkt_id, action, qty, note, actor)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, run_id, produkt_id, action, qty, note, actor, created_at
            """,
            (run_id, produkt_id, action, qty, note, actor),
        )
        row = cur.fetchone()
        if row is None:  # pragma: no cover - INSERT ... RETURNING always yields one row
            msg = "insert into buyer_action returned no row"
            raise StoreError(msg)
        return row


def list_buyer_actions(dsn: str, *, run_id: str | None = None) -> list[dict[str, Any]]:
    """All logged buyer actions, optionally filtered to one run - the Phase-4
    backtest dataset."""
    _require_psycopg()
    sql = "SELECT * FROM buyer_action"
    params: tuple[Any, ...] = ()
    if run_id is not None:
        sql += " WHERE run_id = %s"
        params = (run_id,)
    sql += " ORDER BY created_at DESC"
    with psycopg.connect(dsn, row_factory=dict_row) as cx, cx.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()
