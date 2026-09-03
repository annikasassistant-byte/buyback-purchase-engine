"""Test data factories - synthetic parser tables and feature objects.

Shared support code (not a test module). Keeps every unit test independent of
the real workbook and of every other test.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from purchase_engine.domain.models import ParserTables, ProductFeatures, ProductProfitability

AS_OF = datetime(2026, 8, 24)

DateQtyMargin = tuple[Any, float, float]


# --------------------------------------------------------------------------- #
# Sales rows                                                                  #
# --------------------------------------------------------------------------- #
def sales_rows(
    pid: str,
    kat: str,
    model: str,
    dates_qty_margin: Sequence[DateQtyMargin],
) -> list[dict]:
    rows = []
    for d, qty, margin_frac in dates_qty_margin:
        vk = 200.0 * qty
        ek = vk * (1 - margin_frac)
        rows.append(
            {
                "produkt_id": pid,
                "auftragsdatum": pd.Timestamp(d),
                "menge": qty,
                "gesamt_vk": vk,
                "gesamt_ek": ek,
                "rohgewinn": vk - ek,
                "marge_pct": margin_frac,
                "status": "OK",
                "kategorie": kat,
                "modellschluessel": model,
                "ek_kaufdatum": pd.Timestamp(d),
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# ParserTables                                                                #
# --------------------------------------------------------------------------- #
def build_tables(
    *,
    products: list[tuple[str, str, str, str]] | None = None,
    sales: list[dict] | None = None,
    inventory: list[tuple] | None = None,
    mapping: list[tuple] | None = None,
    ek: list[dict] | None = None,
    merges: list[dict] | None = None,
    workbook_updated: datetime | None = AS_OF,
) -> ParserTables:
    if products is None:
        products = [
            ("BB000001", "PS4", "PS4 SLIM 1TB", "JA"),
            ("BB000002", "PS4", "PS4 PRO 1TB", "JA"),
            ("BB000003", "Kamera", "FZ200", "JA"),
            ("BB000004", "Kamera", "NOSALES CAM", "JA"),
            ("BB000009", "PS4", "PS4 500GB", "NEIN"),  # inactive - must be ignored
        ]
    produktstamm = pd.DataFrame(
        [
            {
                "produkt_id": p,
                "kategorie": k,
                "modellschluessel": m,
                "standardname": m,
                "ek_regel": "STANDARD",
                "aktiv": a,
            }
            for p, k, m, a in products
        ]
    )

    if sales is None:
        fast = [(AS_OF - timedelta(days=i * 2), 2, 0.40) for i in range(1, 9)]  # 16u/30d
        slow = [(AS_OF - timedelta(days=i * 20), 1, 0.55) for i in range(1, 5)]  # 4u/90d
        sales = (
            sales_rows("BB000001", "PS4", "PS4 SLIM 1TB", fast)
            + sales_rows("BB000002", "PS4", "PS4 PRO 1TB", slow)
            + sales_rows("BB000003", "Kamera", "FZ200", slow)
        )
    tagesprofite = (
        pd.DataFrame(sales)
        if sales
        else pd.DataFrame(
            columns=[
                "produkt_id",
                "auftragsdatum",
                "menge",
                "gesamt_vk",
                "gesamt_ek",
                "rohgewinn",
                "marge_pct",
                "status",
                "kategorie",
                "modellschluessel",
                "ek_kaufdatum",
            ]
        )
    )

    if inventory is None:
        inventory = [
            ("BB000001", 0, 1, 1, "PARSER_PIPELINE", "RESERVIERT"),
            ("BB000002", 5, 5, 0, "VERKAUFS_MAPPING", "VERFUEGBAR"),
            ("BB000003", 0, 0, 0, "PARSER_PIPELINE", "OUT_OF_STOCK"),
            # BB000004 deliberately has no inventory row
        ]
    inventar_bestand = pd.DataFrame(
        [
            {
                "produkt_id": p,
                "kategorie": "",
                "modellschluessel": "",
                "standardname": "",
                "verfuegbar": v,
                "auf_lager": al,
                "in_auftraegen": io,
                "join_quelle": jq,
                "verfuegbarkeit": vb,
            }
            for p, v, al, io, jq, vb in inventory
        ]
    )

    if mapping is None:
        mapping = [
            ("BB000001", "PARSER_PIPELINE", "KATEGORIE_UND_MODELLSCHLUESSEL", "JA"),
            ("BB000002", "VERKAUFS_MAPPING", "EINDEUTIGER_MODELLSCHLUESSEL", "JA"),
            ("BB000003", "PARSER_PIPELINE", "KATEGORIE_UND_MODELLSCHLUESSEL", "JA"),
        ]
    inventar_mapping = pd.DataFrame(
        [
            {"produkt_id": p, "join_quelle": j, "zuordnungsquelle": z, "aktiv": a}
            for p, j, z, a in mapping
        ]
    )

    ek_df = (
        pd.DataFrame(ek)
        if ek is not None
        else pd.DataFrame(
            [
                {
                    "kaufdatum": pd.Timestamp(AS_OF - timedelta(days=d)),
                    "kategorie": "PS4",
                    "modellschluessel": "PS4 SLIM 1TB",
                    "gesamtpreis": 90.0,
                    "hauptprodukt_ek": 80.0,
                    "pruefstatus": "AUTOMATISCH_VERWENDBAR",
                }
                for d in (2, 4, 6, 40)
            ]
        )
    )

    zusammenfuehrung = pd.DataFrame(
        merges or [], columns=["Alte Produkt-ID", "Ziel-Produkt-ID", "Status"]
    )
    ek_regeln = pd.DataFrame(columns=["regel_id", "regeltyp", "schluessel", "wert", "aktiv"])

    return ParserTables(
        produktstamm=produktstamm,
        tagesprofite=tagesprofite,
        inventar_bestand=inventar_bestand,
        inventar_mapping=inventar_mapping,
        ek_normalisiert=ek_df,
        zusammenfuehrung=zusammenfuehrung,
        ek_regeln=ek_regeln,
        workbook_updated=workbook_updated,
    )


# --------------------------------------------------------------------------- #
# ProductFeatures                                                             #
# --------------------------------------------------------------------------- #
def _prof(status: str = "CONFIRMED", margin: float = 45.0) -> ProductProfitability:
    return ProductProfitability("BBx", 200.0, 110.0, 90.0, margin, status, "trailing_window")


def mkfeat(
    pid: str,
    *,
    kat: str = "PS4",
    model: str = "M",
    vel: float | None = 0.5,
    win: int = 30,
    u30: float = 15.0,
    u90: float = 30.0,
    dss: int | None = 5,
    joined: bool = True,
    sellable: float | None = 0.0,
    on_hand: float = 1.0,
    in_orders: float = 1.0,
    purchased_today: int = 0,
    older_incoming: int = 0,
    dos: float | None = 2.0,
    avail: str = "RESERVED",
    mquelle: str = "KATEGORIE_UND_MODELLSCHLUESSEL",
    dup: bool = False,
    margin: float = 45.0,
    hist: float | None = 1.0,
    ok_rows: int = 8,
    prof_status: str = "CONFIRMED",
) -> ProductFeatures:
    eff = (sellable or 0) + purchased_today + older_incoming
    return ProductFeatures(
        produkt_id=pid,
        name=pid,
        kategorie=kat,
        modell=model,
        is_duplicate=dup,
        units_30d=u30,
        units_90d=u90,
        daily_velocity=vel,
        velocity_window_days=win,
        days_since_sale=dss,
        inventory_joined=joined,
        current_sellable=sellable,
        on_hand=on_hand,
        in_orders=in_orders,
        purchased_today=purchased_today,
        older_incoming=older_incoming,
        effective_stock=eff,
        days_of_supply=dos,
        availability=avail,
        join_source="PARSER_PIPELINE",
        mapping_quelle=mquelle,
        profitability=_prof(prof_status, margin),
        margin_pct=margin,
        hist_success=hist,
        ok_rows=ok_rows,
    )
