from __future__ import annotations

import pandas as pd

from purchase_engine._normalize import (
    build_merge_redirect,
    normalize_alias,
    parse_de_number,
    parse_de_number_opt,
    resolve_column,
    split_condition_suffix,
)


def test_parse_de_number_variants():
    assert parse_de_number("1.234,56") == 1234.56
    assert parse_de_number("56,35 €") == 56.35
    assert parse_de_number("149") == 149.0
    assert parse_de_number(93.3) == 93.3
    assert parse_de_number("") == 0.0
    assert parse_de_number("abc") == 0.0
    assert parse_de_number(None) == 0.0


def test_parse_de_number_opt_is_none_for_missing():
    assert parse_de_number_opt(None) is None
    assert parse_de_number_opt(float("nan")) is None
    assert parse_de_number_opt("") is None
    assert parse_de_number_opt("nan") is None
    assert parse_de_number_opt("80,00") == 80.0


def test_split_condition_suffix():
    assert split_condition_suffix("11728 - Sehr Gut") == ("11728", "Sehr Gut")
    assert split_condition_suffix("10204 - Gut") == ("10204", "Gut")
    assert split_condition_suffix("620") == ("620", "")


def test_normalize_alias_folds_umlauts():
    assert normalize_alias("Über Grün (A)") == "ueber gruen a"


def test_resolve_column_is_encoding_tolerant():
    df = pd.DataFrame(columns=["﻿In Aufträgen", "Verfuegbar", "Produkt-ID"])
    assert resolve_column(df, "In Aufträgen", "In Auftraegen") == "﻿In Aufträgen"
    assert resolve_column(df, "Verfügbar", "Verfuegbar") == "Verfuegbar"
    assert resolve_column(df, "produkt_id", "Produkt-ID") == "Produkt-ID"
    df2 = pd.DataFrame(columns=["Gesamtpreis inkl. Versand/Gebühren"])
    assert resolve_column(df2, "Gesamtpreis") == "Gesamtpreis inkl. Versand/Gebühren"


def test_resolve_column_raises_when_required_and_absent():
    df = pd.DataFrame(columns=["a", "b"])
    try:
        resolve_column(df, "missing")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected KeyError")
    assert resolve_column(df, "missing", required=False) is None


def test_merge_redirect_follows_chains_and_ignores_unexecuted():
    mg = pd.DataFrame(
        [
            {"Alte Produkt-ID": "BB000A", "Ziel-Produkt-ID": "BB000B", "Status": "AUSGEFUEHRT"},
            {"Alte Produkt-ID": "BB000B", "Ziel-Produkt-ID": "BB000C", "Status": "AUSGEFUEHRT"},
            {"Alte Produkt-ID": "BB000X", "Ziel-Produkt-ID": "BB000Y", "Status": "VORSCHLAG"},
        ]
    )
    redirect = build_merge_redirect(mg)
    assert redirect["BB000A"] == "BB000C"
    assert redirect["BB000B"] == "BB000C"
    assert "BB000X" not in redirect
