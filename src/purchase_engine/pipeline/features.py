"""Feature calculation - one :class:`ProductFeatures` per active product.

Every feature traces to exactly one parser output table (the plan's "Where every
number in a recommendation comes from" contract). Nothing is invented and
nothing is re-parsed.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from purchase_engine._normalize import build_merge_redirect, clean_text, parse_de_number
from purchase_engine.config import EngineConfig
from purchase_engine.domain.models import ParserTables, ProductFeatures
from purchase_engine.domain.ports import IncomingCounts, IncomingStockSource, Profitability

_AVAIL_MAP = {
    "VERFUEGBAR": "AVAILABLE",
    "VERFÜGBAR": "AVAILABLE",
    "AVAILABLE": "AVAILABLE",
    "RESERVIERT": "RESERVED",
    "RESERVED": "RESERVED",
    "LOW_STOCK": "LOW_STOCK",
    "NIEDRIG": "LOW_STOCK",
    "OUT_OF_STOCK": "OUT_OF_STOCK",
    "AUSVERKAUFT": "OUT_OF_STOCK",
}

# best -> worst; used to pick one mapping source per product
_MAPPING_RANK = (
    "ALIAS",
    "EINDEUTIGER_MODELLSCHLUESSEL",
    "ARTIKELNUMMER_OVERRIDE_KATEGORIE_UND_MODELLSCHLUESSEL",
    "ARTIKELNUMMER_OVERRIDE",
    "KATEGORIE_UND_MODELLSCHLUESSEL",
    "NEUBEWERTUNG_OHNE_EINDEUTIGEN_TREFFER",
)


def resolve_as_of(tables: ParserTables, cfg: EngineConfig) -> datetime:
    """Config ``as_of`` if pinned, else the latest sale date in Tagesprofite."""
    if cfg.data.as_of:
        return datetime.fromisoformat(str(cfg.data.as_of))
    dates = pd.to_datetime(tables.tagesprofite["auftragsdatum"], errors="coerce").dropna()
    return dates.max().to_pydatetime() if len(dates) else datetime.now()


def _derive_availability(
    inv_row: pd.Series | None, current: float | None, on_hand: float | None
) -> str:
    if inv_row is None:
        return "UNAVAILABLE_STOCK"
    badge = _AVAIL_MAP.get(clean_text(inv_row.get("verfuegbarkeit")).upper())
    if badge:
        return badge
    if (on_hand or 0) <= 0:
        return "OUT_OF_STOCK"
    if (current or 0) <= 0:
        return "RESERVED"
    if (current or 0) <= 1:
        return "LOW_STOCK"
    return "AVAILABLE"


class FeatureBuilder:
    """Turn :class:`ParserTables` into a list of :class:`ProductFeatures`."""

    def __init__(
        self,
        tables: ParserTables,
        cfg: EngineConfig,
        incoming: IncomingStockSource,
        profitability: Profitability,
    ) -> None:
        self.tables = tables
        self.cfg = cfg
        self.incoming = incoming
        self.profitability = profitability
        self.last_incoming: IncomingCounts | None = None

    # -- internal prep ---------------------------------------------------- #
    def _active_products(self) -> pd.DataFrame:
        ps = self.tables.produktstamm
        act = ps[(ps["aktiv"] == "JA") & ps["produkt_id"].astype(str).str.startswith("BB")].copy()
        act["_composite"] = act["kategorie"].str.upper() + "|" + act["modellschluessel"].str.upper()
        dup_counts = act.groupby("_composite")["produkt_id"].transform("nunique")
        act["is_duplicate"] = dup_counts > 1
        return act

    def _mapping_quelle_by_pid(self) -> dict[str, str]:
        im = self.tables.inventar_mapping
        out: dict[str, str] = {}
        if im is not None and not im.empty:
            active = im[im["aktiv"] == "JA"] if "aktiv" in im.columns else im
            for pid, grp in active.groupby("produkt_id"):
                vals = {str(v).upper() for v in grp["zuordnungsquelle"] if clean_text(v)}
                best = next((c for c in _MAPPING_RANK if c in vals), "")
                if not best and vals:
                    best = sorted(vals)[0]
                out[str(pid)] = best
        for pid in self.tables.inventar_bestand["produkt_id"]:
            out.setdefault(str(pid), "")
        return out

    def _sales_features(self, as_of: datetime) -> dict[str, dict]:
        vcfg = self.cfg.velocity
        tp = self.tables.tagesprofite.copy()
        redirect = build_merge_redirect(self.tables.zusammenfuehrung)
        tp["produkt_id"] = tp["produkt_id"].map(lambda p: redirect.get(p, p))
        tp["_menge"] = tp["menge"].map(parse_de_number)
        tp["_vk"] = tp["gesamt_vk"].map(parse_de_number)
        tp["_roh"] = tp["rohgewinn"].map(parse_de_number)
        tp["_marge"] = tp["marge_pct"].map(parse_de_number)
        median_marge = tp["_marge"].abs().median(skipna=True)
        if median_marge is not None and median_marge < 2:
            tp["_marge"] = tp["_marge"] * 100.0
        tp["_date"] = pd.to_datetime(tp["auftragsdatum"], errors="coerce")

        ok = tp[
            (tp["status"] == "OK")
            & (tp["_vk"] > 0)
            & tp["produkt_id"].astype(str).str.startswith("BB")
        ].copy()

        d_fast = pd.Timestamp(as_of) - pd.Timedelta(days=vcfg.fast_window_days)
        d_slow = pd.Timestamp(as_of) - pd.Timedelta(days=vcfg.slow_window_days)
        d_marge = pd.Timestamp(as_of) - pd.Timedelta(days=self.cfg.profitability.margin_window_days)

        feats: dict[str, dict] = {}
        for pid, g in ok.groupby("produkt_id"):
            u_fast = float(g.loc[g["_date"] >= d_fast, "_menge"].sum())
            u_slow = float(g.loc[g["_date"] >= d_slow, "_menge"].sum())
            last = g["_date"].max()
            dss = (pd.Timestamp(as_of) - last).days if pd.notna(last) else None
            if u_fast >= vcfg.fast_switch_units:
                vel: float | None = u_fast / vcfg.fast_window_days
                win = vcfg.fast_window_days
            elif u_slow > 0:
                vel, win = u_slow / vcfg.slow_window_days, vcfg.slow_window_days
            else:
                vel, win = None, 0
            mwin = g.loc[g["_date"] >= d_marge, "_marge"]
            margin = (
                float(mwin.median())
                if len(mwin)
                else (float(g["_marge"].median()) if len(g) else None)
            )
            hist_success = float((g["_roh"] > 0).mean()) if len(g) else None
            feats[str(pid)] = {
                "units_fast": u_fast,
                "units_slow": u_slow,
                "daily_velocity": vel,
                "velocity_window_days": win,
                "days_since_sale": dss,
                "margin_pct": margin,
                "hist_success": hist_success,
                "ok_rows": len(g),
            }
        return feats

    # -- public --------------------------------------------------------- #
    def build(self, as_of: datetime) -> list[ProductFeatures]:
        active = self._active_products()
        mapping_quelle = self._mapping_quelle_by_pid()
        sales = self._sales_features(as_of)
        inv_ix = self.tables.inventar_bestand.set_index("produkt_id")
        incoming = self.incoming.counts(as_of)
        self.last_incoming = incoming

        out: list[ProductFeatures] = []
        for _, prow in active.iterrows():
            pid = str(prow["produkt_id"])
            f = sales.get(pid, {})
            inv_row = inv_ix.loc[pid] if pid in inv_ix.index else None
            if isinstance(inv_row, pd.DataFrame):
                inv_row = inv_row.iloc[0]
            joined = inv_row is not None

            current: float | None = None
            on_hand: float | None = None
            in_ord: float | None = None
            if inv_row is not None:
                current = max(float(parse_de_number(inv_row["verfuegbar"])), 0.0)
                on_hand = max(float(parse_de_number(inv_row["auf_lager"])), 0.0)
                in_ord = float(parse_de_number(inv_row["in_auftraegen"]))

            model_key = str(prow["modellschluessel"]).upper()
            purchased_today, older_incoming = incoming.for_model(model_key)

            effective = (current or 0.0) + purchased_today + older_incoming
            vel = f.get("daily_velocity")
            win = int(f.get("velocity_window_days", 0))
            dos = effective / vel if vel and vel > 0 else None

            availability = _derive_availability(inv_row, current, on_hand)
            prof = self.profitability.get_profitability(pid, as_of)

            join_source = str(inv_row["join_quelle"]) if inv_row is not None else "NO_INVENTORY_ROW"
            mq = mapping_quelle.get(pid, "") or (join_source if joined else "")

            out.append(
                ProductFeatures(
                    produkt_id=pid,
                    name=str(prow["standardname"]) or str(prow["modellschluessel"]),
                    kategorie=str(prow["kategorie"]),
                    modell=str(prow["modellschluessel"]),
                    is_duplicate=bool(prow["is_duplicate"]),
                    units_30d=float(f.get("units_fast", 0.0)),
                    units_90d=float(f.get("units_slow", 0.0)),
                    daily_velocity=vel,
                    velocity_window_days=win,
                    days_since_sale=f.get("days_since_sale"),
                    inventory_joined=bool(joined),
                    current_sellable=current,
                    on_hand=on_hand,
                    in_orders=in_ord,
                    purchased_today=int(purchased_today),
                    older_incoming=int(older_incoming),
                    effective_stock=float(effective),
                    days_of_supply=(round(dos, 2) if dos is not None else None),
                    availability=availability,
                    join_source=join_source,
                    mapping_quelle=mq,
                    profitability=prof,
                    margin_pct=f.get("margin_pct"),
                    hist_success=f.get("hist_success"),
                    ok_rows=int(f.get("ok_rows", 0)),
                )
            )
        return out
