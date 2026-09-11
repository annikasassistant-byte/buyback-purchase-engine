"""Request/response shapes. Kept separate from ``domain.models``'s dataclasses
on purpose - those are the engine's internal vocabulary (see their own
docstring: "no pydantic, no ORM"); these are the wire format, allowed to
reshape/flatten for whoever's calling over HTTP.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    """Body for ``POST /runs``. Everything optional - matches the CLI's own
    defaults (``--budget``/``--as-of`` unset -> config default / latest sale
    date)."""

    budget_eur: float | None = Field(default=None, ge=0)
    as_of: date | None = None


class RunSummary(BaseModel):
    """One ``engine_run`` row - returned by ``POST /runs``, ``GET /runs/latest``
    and ``GET /runs/{run_id}`` with an identical shape on purpose, so the
    frontend needs exactly one type for "a run", regardless of which endpoint
    produced it."""

    run_id: str
    generated_at: datetime
    as_of: date
    budget_eur: float | None
    config_hash: str
    stale: bool
    counts: dict[str, int]
    freshness: dict[str, Any]
    inserted_at: datetime


class RecommendationOut(BaseModel):
    """One product's recommendation within a run - the flattened columns plus
    the full ``payload`` (features / score & confidence breakdown / reasons /
    risks) a buyer-facing detail view needs."""

    run_id: str
    produkt_id: str
    name: str
    kategorie: str
    label: str
    purchase_score: int
    confidence: int
    availability: str
    per_sku_capped_qty: int
    recommended_qty: int
    budget_trimmed: bool
    est_unit_ek: float | None
    est_gross_profit_per_eur: float | None
    est_total_cost: float | None
    est_total_gross_profit: float | None
    payload: dict[str, Any]


class AllocateRequest(BaseModel):
    """Body for ``POST /runs/{run_id}/allocate`` - the live budget slider."""

    budget_eur: float = Field(ge=0)


class AllocationLineOut(BaseModel):
    produkt_id: str
    name: str
    kategorie: str
    purchase_score: int
    confidence: int
    availability: str
    final_qty: int
    trimmed: bool
    unit_ek: float | None
    gp_per_eur: float | None
    total_cost: float | None
    total_gross_profit: float | None


class AllocateResponse(BaseModel):
    run_id: str
    budget_eur: float
    lines: list[AllocationLineOut]


class ActionRequest(BaseModel):
    """Body for ``POST /actions`` - one buyer decision, the Phase-4 backtest
    dataset the plan calls for."""

    run_id: str
    produkt_id: str
    action: Literal["BUY", "ADJUST", "SKIP"]
    qty: int | None = Field(default=None, ge=0)
    note: str | None = None
    actor: str | None = None


class ActionOut(BaseModel):
    id: int
    run_id: str
    produkt_id: str
    action: str
    qty: int | None
    note: str | None
    actor: str | None
    created_at: datetime
