"""Text / number helpers that mirror named parser functions, plus a tolerant
column resolver.

The engine never re-parses product names. These helpers exist only to read the
parser's *output* cleanly: German decimal commas, condition suffixes on JTL
article numbers, and Excel headers that arrive with inconsistent umlaut /
whitespace / BOM encoding.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable
from typing import Literal, overload

import pandas as pd

_CONDITIONS = ("Sehr Gut", "Wie Neu", "Akzeptabel", "Neu", "Gut", "Prüfen", "Pruefen")
_CONDITION_RE = re.compile(
    r"^(?P<sku>.+?)\s*-\s*(?P<cond>" + "|".join(re.escape(c) for c in _CONDITIONS) + r")\s*$",
    re.IGNORECASE,
)


def clean_text(value: object) -> str:
    """``salesCleanText_`` - collapse whitespace, drop NBSP / NUL, trim."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = str(value).replace(" ", " ").replace("\x00", "")
    return re.sub(r"\s+", " ", text).strip()


def normalize_alias(value: object) -> str:
    """``productNormalizeAlias_`` - lower-case + umlaut fold, for alias lookups."""
    text = clean_text(value).lower()
    text = (
        text.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
        .replace("–", "-")
        .replace("—", "-")
    )
    text = re.sub(r"[()\[\],;:]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_de_number(value: object) -> float:
    """Parse a German-formatted number ('1.234,56', '56,35 €') -> float. Junk -> 0.0."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("€", "").replace(" ", "").replace(" ", "")
    if not text:
        return 0.0
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_de_number_opt(value: object) -> float | None:
    """Like :func:`parse_de_number` but returns ``None`` for blank / NaN / junk."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        f = float(value)
        return None if math.isnan(f) else f
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return None
    return parse_de_number(value)


def split_condition_suffix(article_number: object) -> tuple[str, str]:
    """JTL stores condition as a suffix on Artikelnummer ('11728 - Sehr Gut')."""
    raw = clean_text(article_number)
    m = _CONDITION_RE.match(raw)
    if m:
        return clean_text(m.group("sku")), clean_text(m.group("cond"))
    return raw, ""


# --------------------------------------------------------------------------- #
# Column resolution                                                           #
# --------------------------------------------------------------------------- #
def canon(name: object) -> str:
    """Fold a column header to a comparison key: NFC, strip BOM, drop every
    non-alphanumeric, fold umlauts, lower-case. 'In Aufträgen', 'In Auftr?gen'
    and 'in_auftraegen' all collapse to 'inauftragen'."""
    text = unicodedata.normalize("NFC", str(name)).replace("﻿", "").replace("�", "")
    text = text.lower()
    text = (
        text.replace("ä", "a")
        .replace("ö", "o")
        .replace("ü", "u")
        .replace("ß", "ss")
        .replace("ae", "a")
        .replace("oe", "o")
        .replace("ue", "u")
    )
    return re.sub(r"[^a-z0-9]", "", text)


@overload
def resolve_column(df: pd.DataFrame, *candidates: str) -> str: ...
@overload
def resolve_column(df: pd.DataFrame, *candidates: str, required: Literal[True]) -> str: ...
@overload
def resolve_column(df: pd.DataFrame, *candidates: str, required: Literal[False]) -> str | None: ...
def resolve_column(df: pd.DataFrame, *candidates: str, required: bool = True) -> str | None:
    """Return the real column in ``df`` matching any candidate (encoding-tolerant).

    Raises ``KeyError`` if ``required`` (the default) and nothing matches.
    """
    lookup = {canon(c): c for c in df.columns}
    for cand in candidates:
        hit = lookup.get(canon(cand))
        if hit is not None:
            return hit
    # substring fallback - handles 'Gesamtpreis inkl. Versand/Gebühren'
    for cand in candidates:
        ck = canon(cand)
        for canon_col, real in lookup.items():
            if ck and ck in canon_col:
                return real
    if required:
        msg = f"none of {candidates!r} found in columns {list(df.columns)!r}"
        raise KeyError(msg)
    return None


def select(df: pd.DataFrame, spec: dict[str, Iterable[str]]) -> pd.DataFrame:
    """Project + rename in one step.

    ``spec``: ``{output_name: (candidate header, ...)}``. Missing optional
    columns (empty candidate tuple or not found) are created as all-None.
    """
    out = pd.DataFrame(index=df.index)
    for out_name, cands in spec.items():
        cand_tuple = tuple(cands)
        col = resolve_column(df, *cand_tuple, required=False) if cand_tuple else None
        out[out_name] = df[col] if col is not None else None
    return out


def de_date(series: pd.Series) -> pd.Series:
    """Parse a column of dates that may be real datetimes or dd.mm.yyyy strings."""
    parsed = pd.to_datetime(series, errors="coerce")
    if parsed.isna().mean() > 0.5:
        parsed = pd.to_datetime(series, errors="coerce", dayfirst=True)
    return parsed


def build_merge_redirect(zusammenfuehrung: pd.DataFrame) -> dict[str, str]:
    """Executed product merges -> ``{old_produkt_id: surviving_produkt_id}``.

    Chains are followed (A->B, B->C  ==>  A->C). SCD-lite: applied to historical
    Produkt-IDs so a merged product's past sales still roll up to the survivor
    even if a given sheet predates the merge.
    """
    if zusammenfuehrung is None or zusammenfuehrung.empty:
        return {}
    old_c = resolve_column(zusammenfuehrung, "Alte Produkt-ID", "alte_produkt_id")
    new_c = resolve_column(zusammenfuehrung, "Ziel-Produkt-ID", "ziel_produkt_id")
    st_c = resolve_column(zusammenfuehrung, "Status", required=False)
    direct: dict[str, str] = {}
    for _, row in zusammenfuehrung.iterrows():
        if st_c is not None and clean_text(row[st_c]).upper() not in {"AUSGEFUEHRT", "AUSGEFÜHRT"}:
            continue
        old, new = clean_text(row[old_c]), clean_text(row[new_c])
        if old and new and old != new:
            direct[old] = new

    def resolve(pid: str) -> str:
        seen: set[str] = set()
        while pid in direct and pid not in seen:
            seen.add(pid)
            pid = direct[pid]
        return pid

    return {old: resolve(old) for old in direct}
