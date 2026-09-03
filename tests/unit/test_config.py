from __future__ import annotations

from pathlib import Path

import pytest

from purchase_engine.config import load_config
from purchase_engine.errors import ConfigError


def test_default_config_loads_and_validates():
    cfg = load_config()
    assert cfg.score.weights["demand"] == 35
    assert 0 <= cfg.score.consider_min <= cfg.score.buy_min <= 100
    assert cfg.incoming.source == "ek_normalisiert"


def test_config_hash_is_stable_and_short():
    a, b = load_config().hash, load_config().hash
    assert a == b
    assert len(a) == 12


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "engine.yml"
    p.write_text(body, encoding="utf-8")
    return p


_MINIMAL = (
    "score:\n"
    "  weights: {demand: 35, inventory_need: 30, profit: 25, market: 10}\n"
    "  labels: {buy_min: 65, consider_min: 40}\n"
    "confidence:\n"
    "  weights: {mapping: 0.30, sales_sufficiency: 0.25, "
    "inventory_reliability: 0.25, profitability_reliability: 0.20}\n"
    "profit_score: {margin_weight: 0.7, hist_success_weight: 0.3}\n"
)


def test_minimal_config_is_accepted(tmp_path: Path):
    cfg = load_config(_write(tmp_path, _MINIMAL))
    assert cfg.quantity.target_coverage_days == 14  # default filled in


def test_missing_file_raises_config_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yml")


def test_invalid_yaml_raises_config_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(_write(tmp_path, "score: [unclosed\n"))


def test_bad_label_bounds_raise(tmp_path: Path):
    body = _MINIMAL.replace("buy_min: 65, consider_min: 40", "buy_min: 40, consider_min: 65")
    with pytest.raises(ConfigError, match="consider_min <= buy_min"):
        load_config(_write(tmp_path, body))


def test_profit_score_weights_must_sum_to_one(tmp_path: Path):
    body = _MINIMAL.replace("hist_success_weight: 0.3", "hist_success_weight: 0.5")
    with pytest.raises(ConfigError, match=r"must be 1\.0"):
        load_config(_write(tmp_path, body))


def test_unknown_incoming_source_rejected(tmp_path: Path):
    body = _MINIMAL + "incoming:\n  source: telepathy\n"
    with pytest.raises(ConfigError, match="not supported"):
        load_config(_write(tmp_path, body))


def test_missing_required_weights_key_raises(tmp_path: Path):
    body = "score:\n  labels: {buy_min: 65, consider_min: 40}\n"
    with pytest.raises(ConfigError, match="missing required key"):
        load_config(_write(tmp_path, body))
