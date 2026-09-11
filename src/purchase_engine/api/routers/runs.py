"""``/runs`` - trigger the real engine, read back what it produced, and the
live budget re-allocation endpoint the buyer's budget field calls.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from purchase_engine.adapters import query
from purchase_engine.adapters.store import PostgresStore
from purchase_engine.adapters.workbook import find_default_workbook
from purchase_engine.api.deps import require_api_key
from purchase_engine.api.schemas import (
    AllocateRequest,
    AllocateResponse,
    AllocationLineOut,
    RecommendationOut,
    RunRequest,
    RunSummary,
)
from purchase_engine.api.settings import get_settings
from purchase_engine.config import load_config
from purchase_engine.errors import PurchaseEngineError
from purchase_engine.pipeline.orchestrator import Engine
from purchase_engine.pipeline.quantity import BudgetAllocator

log = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=RunSummary, status_code=status.HTTP_201_CREATED)
def trigger_run(body: RunRequest) -> dict[str, object]:
    """Run the real engine - the same code path as
    ``python -m purchase_engine --budget ...`` - and persist it to Postgres.

    Synchronous on purpose: a run over the shipped sample workbook (374
    products) measures ~17s, well inside a normal request timeout on a real
    host (unlike Vercel's serverless functions, which is exactly why this
    API isn't deployed there - see ADR 0010). Revisit with a background job
    + polling only if the workbook grows enough to make that untrue.
    """
    settings = get_settings()
    workbook = settings.workbook_path or find_default_workbook()
    if workbook is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "no workbook found - set WORKBOOK_PATH",
        )

    cfg = load_config()
    store = PostgresStore(settings.database_url)
    as_of = datetime.combine(body.as_of, datetime.min.time()) if body.as_of else None

    try:
        result = Engine(cfg, store).run(workbook, as_of=as_of, budget_eur=body.budget_eur)
    except PurchaseEngineError as exc:
        log.error("engine run failed: %s", exc)  # noqa: TRY400 - user-facing, not a traceback
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    run = query.get_run(settings.database_url, result.run_id)
    if run is None:  # pragma: no cover - store.save() just committed this row
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "run vanished after save")
    return run


@router.get("/latest", response_model=RunSummary)
def latest_run() -> dict[str, object]:
    settings = get_settings()
    run = query.get_latest_run(settings.database_url)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no runs yet - POST /runs first")
    return run


@router.get("/{run_id}", response_model=RunSummary)
def get_run(run_id: str) -> dict[str, object]:
    settings = get_settings()
    run = query.get_run(settings.database_url, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id!r} not found")
    return run


@router.get("/{run_id}/recommendations", response_model=list[RecommendationOut])
def get_recommendations(
    run_id: str, label: str | None = Query(default=None, pattern="^(BUY|CONSIDER|SKIP)$")
) -> list[dict[str, object]]:
    settings = get_settings()
    if query.get_run(settings.database_url, run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id!r} not found")
    return query.list_recommendations(settings.database_url, run_id, label=label)


@router.post("/{run_id}/allocate", response_model=AllocateResponse)
def allocate(run_id: str, body: AllocateRequest) -> AllocateResponse:
    """Re-rank the BUY tier for a new budget without re-running the pipeline -
    the live path the frontend's budget field calls on every change. Reuses
    the real ``BudgetAllocator``, not a reimplementation - see
    ``adapters/query.py:load_buy_plans_for_allocation`` and ADR 0010.
    """
    settings = get_settings()
    if query.get_run(settings.database_url, run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id!r} not found")

    inputs = query.load_buy_plans_for_allocation(settings.database_url, run_id)
    cfg = load_config()
    lines = BudgetAllocator(cfg).allocate(
        inputs.buy_plans, {}, inputs.profitability, body.budget_eur
    )

    out = [
        AllocationLineOut(
            produkt_id=pid,
            name=inputs.display[pid]["name"],
            kategorie=inputs.display[pid]["kategorie"],
            purchase_score=inputs.display[pid]["purchase_score"],
            confidence=inputs.display[pid]["confidence"],
            availability=inputs.display[pid]["availability"],
            final_qty=line.final_qty,
            trimmed=line.trimmed,
            unit_ek=line.unit_ek,
            gp_per_eur=line.gp_per_eur,
            total_cost=line.total_cost,
            total_gross_profit=line.total_gross_profit,
        )
        for pid, line in lines.items()
    ]
    out.sort(key=lambda line: (-(line.gp_per_eur or -1.0), -line.purchase_score))
    return AllocateResponse(run_id=run_id, budget_eur=body.budget_eur, lines=out)
