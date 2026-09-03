"""Incoming-stock sources: units already bought but not yet sellable.

Feeds two terms of Effective Stock Position, always kept separate:
``purchased_today`` (Kaufdatum == as_of) and ``older_incoming`` (Kaufdatum within
``as_of - N .. as_of - 1``).

MVP default = :class:`EkNormalisiertIncoming`, a proxy off
``EK_Normalisiert.Kaufdatum`` - the only thing available when just the profit
workbook is supplied. The plan's "live purchase table" (a hand-entered Google
Sheet, same shape once wired in) plugs in behind the same ``IncomingStockSource``
port; see :class:`LivePurchaseTableIncoming`.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

import pandas as pd

from purchase_engine._normalize import clean_text
from purchase_engine.config import IncomingConfig
from purchase_engine.domain.models import ParserTables
from purchase_engine.domain.ports import IncomingCounts
from purchase_engine.errors import IncomingSourceError


class NullIncoming:
    """No incoming-stock signal at all (everything 0). Useful for tests."""

    def counts(self, as_of: datetime) -> IncomingCounts:
        return IncomingCounts(source="none")


class EkNormalisiertIncoming:
    """Proxy off ``EK_Normalisiert.Kaufdatum``.

    Not a true PURCHASED->INCOMING->RECEIVED ledger: double-counting is bounded
    by the rolling window, not reconciled against JTL goods receipts. A bulk lot
    ("11x 3DS XL") counts as 1 row, so these figures are a *floor*.
    """

    def __init__(
        self,
        ek_normalisiert: pd.DataFrame,
        cfg: IncomingConfig,
        known_model_keys: set[str] | None = None,
    ) -> None:
        self._ek = ek_normalisiert
        self._cfg = cfg
        self._known = {k.upper() for k in known_model_keys} if known_model_keys else None

    def counts(self, as_of: datetime) -> IncomingCounts:
        cfg = self._cfg
        out = IncomingCounts(source="ek_normalisiert")
        df = self._ek
        if df is None or df.empty:
            return out

        kd = pd.to_datetime(df["kaufdatum"], errors="coerce")
        lo_sane = pd.Timestamp("2024-01-01")
        hi_sane = pd.Timestamp(as_of) + pd.Timedelta(days=2)
        bad = kd.isna() | (kd < lo_sane) | (kd > hi_sane)
        out.rows_rejected_bad_date = int(bad.sum())

        work = df.loc[~bad].copy()
        work["_kd"] = kd.loc[~bad].dt.normalize()

        if cfg.ek_status_filter:
            want = cfg.ek_status_filter.upper()
            work = work[work["pruefstatus"].map(lambda v: clean_text(v).upper()) == want]

        work["_mk"] = work["modellschluessel"].map(lambda v: clean_text(v).upper())
        work = work[work["_mk"] != ""]
        out.rows_considered = len(work)

        today = pd.Timestamp(as_of).normalize()
        w_lo, w_hi = cfg.older_incoming_window
        today_lo = today - pd.Timedelta(days=cfg.purchased_today_days)
        win_lo = today - pd.Timedelta(days=w_hi)
        win_hi = today - pd.Timedelta(days=w_lo)

        purchased_today: dict[str, int] = defaultdict(int)
        older_incoming: dict[str, int] = defaultdict(int)
        for mk, d in zip(work["_mk"], work["_kd"], strict=True):
            if self._known is not None and mk not in self._known:
                continue
            if today_lo <= d <= today:
                purchased_today[mk] += 1
                out.rows_today += 1
            elif win_lo <= d <= win_hi:
                older_incoming[mk] += 1
                out.rows_window += 1
        out.purchased_today = dict(purchased_today)
        out.older_incoming = dict(older_incoming)
        return out


class LivePurchaseTableIncoming:
    """Placeholder for the hand-entered purchasing Google Sheet.

    Not wired for the MVP (needs an adapter joining the sheet's Brand/Model/
    Capacity columns through the parser's purchase-side text pipeline, plus a
    one-time backfill - see plan, "Known limitations"). Kept here so the seam is
    explicit: swap ``incoming.source: live_purchase_table`` in config and provide
    the joined rows; nothing downstream changes.
    """

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        msg = (
            "live_purchase_table incoming source is not available in the MVP - the profit "
            "workbook is the only supplied dataset. Use incoming.source: ek_normalisiert."
        )
        raise IncomingSourceError(msg)

    def counts(self, as_of: datetime) -> IncomingCounts:  # pragma: no cover
        raise NotImplementedError


def build_incoming_source(
    tables: ParserTables,
    cfg: IncomingConfig,
    known_model_keys: set[str],
) -> EkNormalisiertIncoming | LivePurchaseTableIncoming:
    if cfg.source == "ek_normalisiert":
        return EkNormalisiertIncoming(tables.ek_normalisiert, cfg, known_model_keys)
    if cfg.source == "live_purchase_table":  # pragma: no cover - raises with guidance
        return LivePurchaseTableIncoming()
    msg = f"unknown incoming.source {cfg.source!r}"
    raise IncomingSourceError(msg)
