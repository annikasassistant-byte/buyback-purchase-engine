"""Golden-file test against the real parser workbook.

Runs the full engine on the checked-in dataset with a pinned ``as_of`` and the
default config, then diffs a *stable* summary (counts + the per-product scoring
outputs; volatile fields like ``run_id`` / timestamps stripped) against
``tests/golden/data/summary.json``.

The workbook is the interim sample dataset checked into the repo under
``data/raw/...`` (see docs/adr/0007) - a real export, carrying real
private-seller PII, deliberately tracked as a reviewed fixture. The test still
skips if it can't find the file (e.g. a checkout that hasn't pulled it, or
before it's ever pushed anywhere). CI additionally runs
``pytest -m "not golden"`` as a belt-and-braces exclusion, independent of
whether the file happens to be present - CI is not a place this data should
run, regardless of what's checked out.

Regenerate after an intentional change::

    PE_WRITE_GOLDEN=1 pytest tests/golden -p no:cov
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from purchase_engine.adapters.workbook import find_default_workbook
from purchase_engine.config import load_config
from purchase_engine.pipeline.orchestrator import Engine

_GOLDEN = Path(__file__).resolve().parent / "data" / "summary.json"
_AS_OF = datetime(2026, 8, 24)

pytestmark = pytest.mark.golden


def _summary() -> dict:
    cfg = load_config()
    result = Engine(cfg).run(find_default_workbook(), as_of=_AS_OF, budget_eur=1500.0)
    rows = [
        {
            "produkt_id": r.produkt_id,
            "label": r.label,
            "score": r.purchase_score,
            "confidence": r.confidence,
            "qty": r.recommended_qty,
            "availability": r.availability,
            "demand": None if r.score.demand is None else round(r.score.demand),
            "inv_need": None if r.score.inventory_need is None else round(r.score.inventory_need),
            "profit": None if r.score.profit is None else round(r.score.profit),
            "capped": r.score.single_component_capped,
        }
        for r in sorted(result.recommendations, key=lambda x: x.produkt_id)
    ]
    return {"config_hash": cfg.hash, "counts": result.counts, "rows": rows}


@pytest.mark.skipif(find_default_workbook() is None, reason="real workbook not found")
def test_golden_matches():
    current = _summary()
    if os.environ.get("PE_WRITE_GOLDEN"):
        _GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        pytest.skip("golden regenerated")

    assert _GOLDEN.exists(), "golden missing - run once with PE_WRITE_GOLDEN=1"
    expected = json.loads(_GOLDEN.read_text(encoding="utf-8"))

    assert current["config_hash"] == expected["config_hash"], "config changed - regenerate golden"
    assert current["counts"] == expected["counts"]
    cur = {r["produkt_id"]: r for r in current["rows"]}
    exp = {r["produkt_id"]: r for r in expected["rows"]}
    assert set(cur) == set(exp)
    diffs = [pid for pid in exp if cur[pid] != exp[pid]]
    assert not diffs, f"{len(diffs)} products changed, e.g. {diffs[:5]}"
