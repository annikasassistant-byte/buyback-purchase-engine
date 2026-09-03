"""Read the existing parser's output, read-only.

MVP source = the "BuyBack - Profit" workbook (the offline export the prototype
already used). The engine only ever *reads* these sheets; it never re-parses a
product name or writes back. A Google Sheets adapter with the same ``.load()``
surface is the Phase-3 swap - nothing downstream changes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from purchase_engine._normalize import canon, clean_text, de_date, resolve_column, select
from purchase_engine.domain.models import ParserTables
from purchase_engine.errors import DataSourceError

log = logging.getLogger(__name__)

# Where the default workbook lives, relative to the repo root: the interim
# sample dataset checked into the repo (see docs/adr/0007 - it's the one file
# under data/raw/ that .gitignore deliberately allow-lists; it carries real
# private-seller PII, tracked on purpose as a reviewed fixture). The CLI and
# the golden test both resolve it through `find_default_workbook` below, so
# this glob is defined exactly once.
DEFAULT_WORKBOOK_GLOB = "data/raw/full_dataset_2026_run/BuyBack - Profit*.xlsx"


def find_default_workbook(start: Path | None = None) -> Path | None:
    """Search upward from `start` (default: CWD) for the default workbook.

    Checks `start`, then each parent directory, for anything matching
    `DEFAULT_WORKBOOK_GLOB`. Returns the lexicographically-last match in the
    first directory that has one (a dated filename sorts newest-last), or
    None if no directory up to the filesystem root has a match.
    """
    here = (start or Path.cwd()).resolve()
    for base in (here, *here.parents):
        hits = sorted(base.glob(DEFAULT_WORKBOOK_GLOB))
        if hits:
            return hits[-1]
    return None


class ParserWorkbook:
    """Load and canonicalise the parser-output tabs the engine needs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.exists():
            msg = f"parser workbook not found: {self.path}"
            raise DataSourceError(msg)
        try:
            self._xl = pd.ExcelFile(self.path, engine="openpyxl")
        except Exception as exc:
            msg = f"cannot open workbook {self.path}: {exc}"
            raise DataSourceError(msg) from exc

    # -- helpers --------------------------------------------------------- #
    def _sheet(self, *names: str) -> pd.DataFrame:
        want = {canon(n) for n in names}
        for real in self._xl.sheet_names:
            if canon(real) in want:
                df = pd.read_excel(self._xl, sheet_name=real, dtype=object)
                return df.dropna(how="all")
        msg = f"sheet {names!r} not in workbook (have: {self._xl.sheet_names!r})"
        raise DataSourceError(msg)

    def _sheet_opt(self, *names: str, columns: list[str]) -> pd.DataFrame:
        try:
            return self._sheet(*names)
        except DataSourceError:
            log.warning("optional sheet %s absent - using empty frame", names)
            return pd.DataFrame(columns=columns)

    # -- public surface ------------------------------------------------- #
    def load(self) -> ParserTables:
        produktstamm = self._load_produktstamm()
        tages = self._load_tagesprofite()
        inv = self._load_inventar_bestand()
        inv_map = self._load_inventar_mapping()
        ek_norm = self._load_ek_normalisiert()
        zf = self._sheet_opt(
            "Produkt_Zusammenführung",
            "Produkt_Zusammenfuehrung",
            columns=["Alte Produkt-ID", "Ziel-Produkt-ID", "Status"],
        )
        ek_regeln = self._load_ek_regeln()

        return ParserTables(
            produktstamm=produktstamm,
            tagesprofite=tages,
            inventar_bestand=inv,
            inventar_mapping=inv_map,
            ek_normalisiert=ek_norm,
            zusammenfuehrung=zf,
            ek_regeln=ek_regeln,
            workbook_updated=self._workbook_updated(),
        )

    # -- per-sheet loaders -------------------------------------------- #
    def _load_produktstamm(self) -> pd.DataFrame:
        raw = self._sheet("Produktstamm")
        df = select(
            raw,
            {
                "produkt_id": ("Produkt-ID",),
                "kategorie": ("Kategorie",),
                "modellschluessel": ("Modellschlüssel", "Modellschluessel"),
                "standardname": ("Standardname",),
                "ek_regel": ("EK-Regel",),
                "aktiv": ("Aktiv",),
            },
        )
        df["produkt_id"] = df["produkt_id"].map(clean_text)
        df["aktiv"] = df["aktiv"].map(lambda v: clean_text(v).upper())
        for col in ("kategorie", "modellschluessel", "standardname"):
            df[col] = df[col].map(clean_text)
        return df

    def _load_tagesprofite(self) -> pd.DataFrame:
        raw = self._sheet("Tagesprofite")
        df = select(
            raw,
            {
                "produkt_id": ("Produkt-ID / Regelschlüssel", "Produkt-ID"),
                "auftragsdatum": ("Auftragsdatum",),
                "menge": ("Menge",),
                "gesamt_vk": ("Gesamt-VK",),
                "gesamt_ek": ("Gesamt-EK",),
                "rohgewinn": ("Rohgewinn",),
                "marge_pct": ("Marge %", "Marge"),
                "status": ("Status",),
                "kategorie": ("Kategorie",),
                "modellschluessel": ("Modellschlüssel", "Modellschluessel"),
                "ek_kaufdatum": ("EK-Kaufdatum",),
            },
        )
        df["produkt_id"] = df["produkt_id"].map(clean_text)
        df["status"] = df["status"].map(lambda v: clean_text(v).upper())
        df["auftragsdatum"] = de_date(df["auftragsdatum"])
        df["modellschluessel"] = df["modellschluessel"].map(clean_text)
        return df

    def _load_inventar_bestand(self) -> pd.DataFrame:
        raw = self._sheet("Inventar_Bestand")
        df = select(
            raw,
            {
                "produkt_id": ("Produkt-ID",),
                "kategorie": ("Kategorie",),
                "modellschluessel": ("Modellschlüssel", "Modellschluessel"),
                "standardname": ("Standardname",),
                "verfuegbar": ("Verfügbar", "Verfuegbar"),
                "auf_lager": ("Auf Lager",),
                "in_auftraegen": ("In Aufträgen", "In Auftraegen"),
                "join_quelle": ("Join-Quelle",),
                "verfuegbarkeit": ("Verfügbarkeit", "Verfuegbarkeit"),
            },
        )
        df["produkt_id"] = df["produkt_id"].map(clean_text)
        df["join_quelle"] = df["join_quelle"].map(lambda v: clean_text(v).upper())
        df["verfuegbarkeit"] = df["verfuegbarkeit"].map(lambda v: clean_text(v).upper())
        return df

    def _load_inventar_mapping(self) -> pd.DataFrame:
        raw = self._sheet_opt(
            "Inventar_Mapping",
            columns=["produkt_id", "join_quelle", "zuordnungsquelle", "aktiv"],
        )
        if not len(raw):
            return raw
        df = select(
            raw,
            {
                "produkt_id": ("Produkt-ID",),
                "join_quelle": ("Join-Quelle",),
                "zuordnungsquelle": ("Zuordnungsquelle",),
                "aktiv": ("Aktiv",),
            },
        )
        df["produkt_id"] = df["produkt_id"].map(clean_text)
        for col in ("zuordnungsquelle", "join_quelle", "aktiv"):
            df[col] = df[col].map(lambda v: clean_text(v).upper())
        return df

    def _load_ek_normalisiert(self) -> pd.DataFrame:
        raw = self._sheet("EK_Normalisiert")
        df = select(
            raw,
            {
                "kaufdatum": ("Kaufdatum",),
                "kategorie": ("Kategorie",),
                "modellschluessel": ("Modellschlüssel", "Modellschluessel"),
                "gesamtpreis": ("Gesamtpreis inkl. Versand/Gebühren", "Gesamtpreis"),
                "hauptprodukt_ek": ("Bereinigter Hauptprodukt-EK", "Hauptprodukt-EK"),
                "pruefstatus": ("Prüfstatus", "Pruefstatus"),
            },
        )
        df["kaufdatum"] = de_date(df["kaufdatum"])
        df["modellschluessel"] = df["modellschluessel"].map(clean_text)
        df["pruefstatus"] = df["pruefstatus"].map(lambda v: clean_text(v).upper())
        return df

    def _load_ek_regeln(self) -> pd.DataFrame:
        raw = self._sheet_opt(
            "EK_Regeln", columns=["regel_id", "regeltyp", "schluessel", "wert", "aktiv"]
        )
        if not len(raw):
            return raw
        return select(
            raw,
            {
                "regel_id": ("Regel-ID",),
                "regeltyp": ("Regeltyp",),
                "schluessel": ("Schlüssel", "Schluessel"),
                "wert": ("Wert",),
                "aktiv": ("Aktiv",),
            },
        )

    def _workbook_updated(self) -> datetime | None:
        try:
            raw = self._sheet("Tagesprofite")
        except DataSourceError:  # pragma: no cover - Tagesprofite is required elsewhere
            return None
        col = resolve_column(raw, "Aktualisiert am", required=False)
        if col is None:
            return None
        vals = de_date(raw[col]).dropna()
        return vals.max().to_pydatetime() if len(vals) else None
