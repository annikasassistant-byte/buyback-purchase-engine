"""Load + validate ``config/engine.yml`` into typed objects.

Mirrors the plan's rule for the future ``Purchase_Engine_Config`` sheet tab:
read and validate every run; on an invalid edit, raise :class:`ConfigError` so
the caller can fall back to the last-good config rather than run on garbage.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from purchase_engine.errors import ConfigError

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "engine.yml"


@dataclass(frozen=True)
class ScoreConfig:
    weights: dict[str, float]
    single_component_cap: float | None
    overstock_supply_multiple: float
    overstock_min_days_since_sale: float
    overstock_max_points: float
    buy_min: float
    consider_min: float


@dataclass(frozen=True)
class ConfidenceConfig:
    weights: dict[str, float]
    mapping_points: dict[str, float]
    profitability_status_points: dict[str, float]
    units_target: float
    units_points: float
    recency_points: float
    recent_days: float
    stale_days: float
    # How many points to dock when the Purchase Score itself only had 1 or 2
    # of its 3 "real" components (demand / inventory need / profit) to work
    # with - see docs/adr/0008.
    evidence_breadth_one_component_penalty: float
    evidence_breadth_two_components_penalty: float


@dataclass(frozen=True)
class QuantityConfig:
    target_coverage_days: float
    lead_time_days: tuple[float, float]
    per_sku_max_exposure: int
    per_category_max_exposure: dict[str, int]


@dataclass(frozen=True)
class BudgetConfig:
    daily_budget_eur: float | None
    rank_low_confidence_normally: bool


@dataclass(frozen=True)
class ProfitabilityConfig:
    ek_lookback_days: int
    ek_max_purchases: int
    margin_window_days: int
    min_ok_rows_confirmed: int
    min_ok_rows_temp: int
    ek_sanity_max_eur: float


@dataclass(frozen=True)
class VelocityConfig:
    fast_window_days: int
    slow_window_days: int
    fast_switch_units: int


@dataclass(frozen=True)
class IncomingConfig:
    source: str
    purchased_today_days: int
    older_incoming_window: tuple[int, int]
    ek_status_filter: str | None


@dataclass(frozen=True)
class DataConfig:
    as_of: str | None
    stale_after_days: int


@dataclass(frozen=True)
class EngineConfig:
    data: DataConfig
    score: ScoreConfig
    profit_score_margin_weight: float
    profit_score_hist_success_weight: float
    confidence: ConfidenceConfig
    velocity: VelocityConfig
    quantity: QuantityConfig
    budget: BudgetConfig
    profitability: ProfitabilityConfig
    incoming: IncomingConfig
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def hash(self) -> str:
        blob = json.dumps(self.raw, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _req(d: dict[str, Any], *path: str) -> Any:
    cur: Any = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            msg = f"config: missing required key '{'.'.join(path)}'"
            raise ConfigError(msg)
        cur = cur[key]
    return cur


def _validate(cfg: EngineConfig) -> EngineConfig:
    for name, weights in (("score", cfg.score.weights), ("confidence", cfg.confidence.weights)):
        if not weights or any(v < 0 for v in weights.values()):
            msg = f"config: {name}.weights must be present and non-negative"
            raise ConfigError(msg)
    if sum(cfg.score.weights.values()) <= 0:
        raise ConfigError("config: score.weights sum to zero")
    if not 0 <= cfg.score.consider_min <= cfg.score.buy_min <= 100:
        raise ConfigError("config: need 0 <= consider_min <= buy_min <= 100")
    if cfg.quantity.target_coverage_days <= 0:
        raise ConfigError("config: quantity.target_coverage_days must be > 0")
    if cfg.quantity.per_sku_max_exposure <= 0:
        raise ConfigError("config: quantity.per_sku_max_exposure must be > 0")
    if cfg.velocity.fast_switch_units < 1:
        raise ConfigError("config: velocity.fast_switch_units must be >= 1")
    one_pen = cfg.confidence.evidence_breadth_one_component_penalty
    two_pen = cfg.confidence.evidence_breadth_two_components_penalty
    if one_pen < 0 or two_pen < 0:
        raise ConfigError("config: confidence.evidence_breadth_penalty values must be >= 0")
    if one_pen < two_pen:
        raise ConfigError(
            "config: evidence_breadth_penalty.one_component must be >= two_components "
            "(less evidence should never be penalised less)"
        )
    if cfg.incoming.source not in {"ek_normalisiert", "live_purchase_table"}:
        msg = f"config: incoming.source {cfg.incoming.source!r} not supported"
        raise ConfigError(msg)
    ps_sum = cfg.profit_score_margin_weight + cfg.profit_score_hist_success_weight
    if abs(ps_sum - 1.0) > 1e-6:
        raise ConfigError("config: profit_score margin_weight + hist_success_weight must be 1.0")
    return cfg


def load_config(path: str | Path | None = None) -> EngineConfig:
    """Read, parse and validate the engine config. Raises :class:`ConfigError`."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        msg = f"config: file not found: {cfg_path}"
        raise ConfigError(msg) from exc
    except yaml.YAMLError as exc:
        msg = f"config: invalid YAML in {cfg_path}: {exc}"
        raise ConfigError(msg) from exc

    data = raw.get("data", {})
    score = raw.get("score", {})
    over = score.get("overstock_penalty", {})
    labels = score.get("labels", {})
    ps = raw.get("profit_score", {})
    conf = raw.get("confidence", {})
    ss = conf.get("sales_sufficiency", {})
    vel = raw.get("velocity", {})
    qty = raw.get("quantity", {})
    bud = raw.get("budget", {})
    prof = raw.get("profitability", {})
    inc = raw.get("incoming", {})

    cfg = EngineConfig(
        data=DataConfig(
            as_of=data.get("as_of"),
            stale_after_days=int(data.get("stale_after_days", 3)),
        ),
        score=ScoreConfig(
            weights={k: float(v) for k, v in _req(raw, "score", "weights").items()},
            single_component_cap=(
                None
                if score.get("single_component_cap") is None
                else float(score["single_component_cap"])
            ),
            overstock_supply_multiple=float(over.get("supply_multiple", 3)),
            overstock_min_days_since_sale=float(over.get("min_days_since_sale", 60)),
            overstock_max_points=float(over.get("max_points", 20)),
            buy_min=float(labels.get("buy_min", 65)),
            consider_min=float(labels.get("consider_min", 40)),
        ),
        profit_score_margin_weight=float(ps.get("margin_weight", 0.7)),
        profit_score_hist_success_weight=float(ps.get("hist_success_weight", 0.3)),
        confidence=ConfidenceConfig(
            weights={k: float(v) for k, v in _req(raw, "confidence", "weights").items()},
            mapping_points={k: float(v) for k, v in conf.get("mapping_points", {}).items()},
            profitability_status_points={
                str(k): float(v) for k, v in conf.get("profitability_status_points", {}).items()
            },
            units_target=float(ss.get("units_target", 10)),
            units_points=float(ss.get("units_points", 70)),
            recency_points=float(ss.get("recency_points", 30)),
            recent_days=float(ss.get("recent_days", 14)),
            stale_days=float(ss.get("stale_days", 180)),
            evidence_breadth_one_component_penalty=float(
                conf.get("evidence_breadth_penalty", {}).get("one_component", 15)
            ),
            evidence_breadth_two_components_penalty=float(
                conf.get("evidence_breadth_penalty", {}).get("two_components", 5)
            ),
        ),
        velocity=VelocityConfig(
            fast_window_days=int(vel.get("fast_window_days", 30)),
            slow_window_days=int(vel.get("slow_window_days", 90)),
            fast_switch_units=int(vel.get("fast_switch_units", 3)),
        ),
        quantity=QuantityConfig(
            target_coverage_days=float(qty.get("target_coverage_days", 14)),
            lead_time_days=_pair(qty.get("lead_time_days", [2, 7]), (2.0, 7.0)),
            per_sku_max_exposure=int(qty.get("per_sku_max_exposure", 8)),
            per_category_max_exposure={
                str(k): int(v) for k, v in (qty.get("per_category_max_exposure") or {}).items()
            },
        ),
        budget=BudgetConfig(
            daily_budget_eur=(
                None if bud.get("daily_budget_eur") is None else float(bud["daily_budget_eur"])
            ),
            rank_low_confidence_normally=bool(bud.get("rank_low_confidence_normally", True)),
        ),
        profitability=ProfitabilityConfig(
            ek_lookback_days=int(prof.get("ek_lookback_days", 60)),
            ek_max_purchases=int(prof.get("ek_max_purchases", 3)),
            margin_window_days=int(prof.get("margin_window_days", 90)),
            min_ok_rows_confirmed=int(prof.get("min_ok_rows_confirmed", 5)),
            min_ok_rows_temp=int(prof.get("min_ok_rows_temp", 1)),
            ek_sanity_max_eur=float(prof.get("ek_sanity_max_eur", 5000)),
        ),
        incoming=IncomingConfig(
            source=str(inc.get("source", "ek_normalisiert")),
            purchased_today_days=int(inc.get("purchased_today_days", 0)),
            older_incoming_window=_int_pair(inc.get("older_incoming_window", [1, 10]), (1, 10)),
            ek_status_filter=inc.get("ek_status_filter"),
        ),
        raw=raw,
    )
    return _validate(cfg)


def _pair(value: Any, default: tuple[float, float]) -> tuple[float, float]:
    try:
        a, b = list(value)[:2]
        return float(a), float(b)
    except (TypeError, ValueError):
        return default


def _int_pair(value: Any, default: tuple[int, int]) -> tuple[int, int]:
    try:
        a, b = list(value)[:2]
        return int(a), int(b)
    except (TypeError, ValueError):
        return default
