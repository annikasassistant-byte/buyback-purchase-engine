"""Command-line entry point.

::

    python -m purchase_engine --budget 1500
    python -m purchase_engine --workbook "/path/to/BuyBack - Profit ....xlsx" --as-of 2026-08-24
    purchase-engine --json > run.json          # console-script, after `pip install`

Writes the append-only history under ``./artifacts`` (override with ``--artifacts``).
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

from purchase_engine import __version__
from purchase_engine.adapters.store import FileStore, MultiStore, SqliteStore
from purchase_engine.adapters.workbook import DEFAULT_WORKBOOK_GLOB, find_default_workbook
from purchase_engine.config import load_config
from purchase_engine.domain.models import Recommendation, RecommendationSet, to_jsonable
from purchase_engine.errors import PurchaseEngineError
from purchase_engine.logconfig import configure_logging
from purchase_engine.pipeline.orchestrator import Engine

log = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="purchase-engine",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "--workbook",
        type=Path,
        default=None,
        help="path to the 'BuyBack - Profit' xlsx (default: search upward from CWD)",
    )
    p.add_argument("--config", type=Path, default=None, help="config/engine.yml override")
    p.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="YYYY-MM-DD; default = latest sale date in Tagesprofite",
    )
    p.add_argument("--budget", type=float, default=None, help="daily purchasing budget in EUR")
    p.add_argument(
        "--artifacts", type=Path, default=Path("artifacts"), help="history output directory"
    )
    p.add_argument(
        "--sqlite", action="store_true", help="also mirror into artifacts/history.sqlite"
    )
    p.add_argument("--no-store", action="store_true", help="do not persist this run")
    p.add_argument("--limit", type=int, default=25, help="rows to print for BUY/CONSIDER")
    p.add_argument("--json", action="store_true", help="print the full RecommendationSet as JSON")
    p.add_argument("-v", "--verbose", action="count", default=0, help="-v info, -vv debug")
    return p


def _fmt_row(r: Recommendation) -> str:
    dv = r.features.daily_velocity or 0.0
    stock = "-" if r.features.current_sellable is None else f"{r.features.current_sellable:.0f}"
    inc = r.features.purchased_today + r.features.older_incoming
    return (
        f"  {r.purchase_score:3d}  conf {r.confidence:3d}%  "
        f"{r.label:8s} qty {r.recommended_qty:2d}  {r.produkt_id}  "
        f"{r.name[:40]:40}  {r.availability:16s} vel={dv:.3f} stock={stock} inc={inc}"
    )


def _print_report(result: RecommendationSet, cfg_fast_switch: int, limit: int) -> None:
    c = result.counts
    print(f"\nrun {result.run_id}")
    budget = f"€{result.budget_eur:,.0f}" if result.budget_eur else "none"
    print(f"as of {result.as_of}  |  budget {budget}  |  config {result.config_hash}")
    if result.data_freshness.stale:
        print(f"  !! {result.data_freshness.note}")
    print(
        f"\nscored {c['scored']}   BUY {c['buy']} "
        f"({c['buy_funded']} funded, {c['buy_actionable']} actionable "
        f">= {cfg_fast_switch} sales/30d)   CONSIDER {c['consider']}   SKIP {c['skip']}"
    )
    print(
        f"inventory-joined {c['inventory_joined']}/{c['scored']}   "
        f"incoming rows: today {c['incoming_rows_today']}, window {c['incoming_rows_window']}"
    )

    print("\n== BUY " + "=" * 72)
    for r in result.by_label("BUY")[:limit]:
        print(_fmt_row(r))
    print("\n== CONSIDER (top) " + "=" * 62)
    for r in result.by_label("CONSIDER")[: max(0, limit // 2)]:
        print(_fmt_row(r))

    buys = result.by_label("BUY")
    hero = next((r for r in buys if r.recommended_qty > 0), None) or next(iter(buys), None)
    if hero:
        print(f"\nhero  {hero.produkt_id}  {hero.name}")
        for line in hero.reasons:
            print(f"   +  {line}")
        for line in hero.risks:
            print(f"   -  {line}")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    configure_logging(args.verbose)

    workbook = args.workbook or find_default_workbook()
    if workbook is None:
        log.error("no --workbook given and none found under CWD (%s)", DEFAULT_WORKBOOK_GLOB)
        return 2

    try:
        cfg = load_config(args.config)
        store = None
        if not args.no_store:
            stores: list[object] = [FileStore(args.artifacts)]
            if args.sqlite:
                stores.append(SqliteStore(Path(args.artifacts) / "history.sqlite"))
            store = MultiStore(*stores)

        as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
        result = Engine(cfg, store).run(workbook, as_of=as_of, budget_eur=args.budget)
    except PurchaseEngineError as exc:
        log.error("%s", exc)  # noqa: TRY400 - user-facing message, not a traceback
        return 1

    if args.json:
        print(json.dumps(to_jsonable(result), ensure_ascii=False, indent=2))
        return 0

    _print_report(result, cfg.velocity.fast_switch_units, args.limit)
    if store is not None:
        print(f"\nwrote history -> {args.artifacts}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
