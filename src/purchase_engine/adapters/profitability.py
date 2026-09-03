"""Profitability adapters.

The scoring / confidence code only ever sees :class:`ProductProfitability`
(6 fields, fixed status vocabulary). The MVP implementation is
:class:`TrailingWindowProfitability` - it reproduces the parser's trusted EK
method ("average of the last N purchases within M days") and a trailing-window
median margin on Status=OK sales. The real Profit Engine later becomes a
different class behind the same ``get_profitability(produkt_id, as_of)`` call;
``source`` is the only field that changes, and scoring never branches on it.
"""

from __future__ import annotations

import math
from datetime import datetime

import pandas as pd

from purchase_engine._normalize import parse_de_number, parse_de_number_opt
from purchase_engine.config import ProfitabilityConfig
from purchase_engine.domain.models import ProductProfitability


def _median(values: list[float | None]) -> float | None:
    vals = sorted(v for v in values if v is not None and not math.isnan(v))
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0


class TrailingWindowProfitability:
    """MVP profitability: trailing-window margin + parser-style EK averaging."""

    source = "trailing_window"

    def __init__(
        self,
        tagesprofite: pd.DataFrame,
        ek_normalisiert: pd.DataFrame,
        model_key_by_pid: dict[str, str],
        cfg: ProfitabilityConfig,
    ) -> None:
        self._cfg = cfg
        self._model_key_by_pid = {k: (v or "").upper() for k, v in model_key_by_pid.items()}

        tp = tagesprofite.copy()
        tp["_vk"] = tp["gesamt_vk"].map(parse_de_number)
        tp["_menge"] = tp["menge"].map(parse_de_number)
        tp["_roh"] = tp["rohgewinn"].map(parse_de_number)
        tp["_marge"] = tp["marge_pct"].map(parse_de_number)
        median_marge = tp["_marge"].abs().median(skipna=True)
        if median_marge is not None and median_marge < 2:  # fraction -> percent
            tp["_marge"] = tp["_marge"] * 100.0
        tp["_date"] = pd.to_datetime(tp["auftragsdatum"], errors="coerce")
        self._ok = tp[
            (tp["status"] == "OK")
            & (tp["_vk"] > 0)
            & tp["produkt_id"].astype(str).str.startswith("BB")
        ].copy()

        ek = ek_normalisiert.copy()
        ek["_date"] = pd.to_datetime(ek["kaufdatum"], errors="coerce")
        ek["_ek_main"] = ek["hauptprodukt_ek"].map(parse_de_number_opt)
        ek["_ek_total"] = ek["gesamtpreis"].map(parse_de_number_opt)
        ek["_mk"] = ek["modellschluessel"].astype(str).str.upper().str.strip()
        sane = (
            ek["_date"].notna()
            & (ek["_date"] >= pd.Timestamp("2024-01-01"))
            & (ek["_date"] <= pd.Timestamp(datetime.now()) + pd.Timedelta(days=2))
        )
        self._ek = ek[sane & (ek["_mk"] != "")].copy()

    # -- helpers ------------------------------------------------------- #
    def _expected_ek(self, model_key: str, as_of: datetime) -> float | None:
        cfg = self._cfg
        rows = self._ek[self._ek["_mk"] == model_key]
        if rows.empty:
            return None

        def unit_ek(row: pd.Series) -> float | None:
            val = row["_ek_main"]
            if val is None or val <= 0:
                val = row["_ek_total"]
            if val is None or val <= 0 or val > cfg.ek_sanity_max_eur:
                return None
            return float(val)

        lo = pd.Timestamp(as_of) - pd.Timedelta(days=cfg.ek_lookback_days)
        recent = rows[(rows["_date"] >= lo) & (rows["_date"] <= pd.Timestamp(as_of))]
        recent = recent.sort_values("_date", ascending=False)
        picks = [e for e in (unit_ek(r) for _, r in recent.iterrows()) if e is not None]
        picks = picks[: cfg.ek_max_purchases]
        if not picks:  # fall back to the single most recent sane purchase, ever
            for _, r in rows.sort_values("_date", ascending=False).iterrows():
                e = unit_ek(r)
                if e is not None:
                    picks = [e]
                    break
        if not picks:
            return None
        return sum(picks) / len(picks)

    def _trailing_ok(self, produkt_id: str, as_of: datetime) -> pd.DataFrame:
        g = self._ok[self._ok["produkt_id"] == produkt_id]
        if g.empty:
            return g
        lo = pd.Timestamp(as_of) - pd.Timedelta(days=self._cfg.margin_window_days)
        win = g[(g["_date"] >= lo) & (g["_date"] <= pd.Timestamp(as_of))]
        return win if not win.empty else g

    # -- port ------------------------------------------------------- #
    def get_profitability(self, produkt_id: str, as_of: datetime) -> ProductProfitability:
        cfg = self._cfg
        all_ok = self._ok[self._ok["produkt_id"] == produkt_id]
        ok_rows = len(all_ok)
        win = self._trailing_ok(produkt_id, as_of)

        margin_pct = _median(list(win["_marge"])) if not win.empty else None
        unit_vk_values = (
            [(vk / m) for vk, m in zip(win["_vk"], win["_menge"], strict=True) if m and m > 0]
            if not win.empty
            else []
        )
        expected_vk = _median(unit_vk_values)

        model_key = self._model_key_by_pid.get(produkt_id, "")
        expected_ek = self._expected_ek(model_key, as_of) if model_key else None

        expected_gp: float | None = None
        if expected_vk is not None and expected_ek is not None:
            expected_gp = expected_vk - expected_ek
        elif expected_vk is not None and margin_pct is not None:
            expected_gp = expected_vk * (margin_pct / 100.0)
        if expected_gp is not None and margin_pct is None and expected_vk:
            margin_pct = 100.0 * expected_gp / expected_vk

        if ok_rows >= cfg.min_ok_rows_confirmed:
            status = "CONFIRMED"
        elif ok_rows >= cfg.min_ok_rows_temp:
            status = "TEMP_CALCULATED"
        else:
            status = "UNAVAILABLE"

        return ProductProfitability(
            produkt_id=produkt_id,
            expected_vk=expected_vk,
            expected_ek=expected_ek,
            expected_gross_profit=expected_gp,
            margin_pct=margin_pct,
            status=status,
            source=self.source,
        )


class StaticProfitability:
    """Test double / manual override: hand it a dict of :class:`ProductProfitability`."""

    source = "static"

    def __init__(self, table: dict[str, ProductProfitability]) -> None:
        self._table = table

    def get_profitability(self, produkt_id: str, as_of: datetime) -> ProductProfitability:
        return self._table.get(
            produkt_id,
            ProductProfitability(
                produkt_id=produkt_id,
                expected_vk=None,
                expected_ek=None,
                expected_gross_profit=None,
                margin_pct=None,
                status="UNAVAILABLE",
                source=self.source,
            ),
        )
