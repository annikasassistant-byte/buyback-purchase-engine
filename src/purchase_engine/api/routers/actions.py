"""``/actions`` - BUY / ADJUST / SKIP, logged from the buyer UI. This is the
Phase-4 backtest dataset the plan calls for: what a buyer actually did with
each recommendation, joinable back to ``recommendation`` on
``(run_id, produkt_id)``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from purchase_engine.adapters import query
from purchase_engine.api.deps import require_api_key
from purchase_engine.api.schemas import ActionOut, ActionRequest
from purchase_engine.api.settings import get_settings

router = APIRouter(prefix="/actions", tags=["actions"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=ActionOut, status_code=status.HTTP_201_CREATED)
def log_action(body: ActionRequest) -> dict[str, object]:
    settings = get_settings()
    if query.get_run(settings.database_url, body.run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {body.run_id!r} not found")
    return query.insert_buyer_action(
        settings.database_url,
        run_id=body.run_id,
        produkt_id=body.produkt_id,
        action=body.action,
        qty=body.qty,
        note=body.note,
        actor=body.actor,
    )


@router.get("", response_model=list[ActionOut])
def list_actions(run_id: str | None = Query(default=None)) -> list[dict[str, object]]:
    settings = get_settings()
    return query.list_buyer_actions(settings.database_url, run_id=run_id)
