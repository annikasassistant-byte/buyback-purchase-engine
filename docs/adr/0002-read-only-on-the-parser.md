# 2. The engine is read-only on the parser

Date: 2026-09-03

## Status

Accepted

## Context

Product identity (purchase ↔ sales ↔ inventory) is resolved by an existing
Google Apps Script parser owned and actively maintained by another developer. It
already links purchase and sales data through a shared model key and, as of the
2026-09 workbook, also rolls inventory up to `Produkt-ID` in `Inventar_Bestand`.
The stakeholder instruction is explicit: **reuse the parser, never replace it**,
and do not modify it for the Purchase Engine's sake.

The engine needs product identity, velocity, margin and stock - all of which the
parser already produces.

## Decision

The engine consumes the parser's **output sheets only**, through
`adapters/workbook.py`. It never:

- re-parses or re-normalises a product name (the `_normalize.py` helpers only
  clean values for *reading* - German numbers, condition suffixes, encoding);
- writes back to any parser sheet;
- depends on the parser's inputs (raw JTL exports, the purchasing sheet).

New identity/matching logic that the engine would benefit from is raised with
the parser's owner as a change to the parser, not implemented here.

## Consequences

- The parser and the engine iterate independently; no coordination cost on
  either side for routine changes.
- Known parser gaps (e.g. "Xbox OneS" not matching "Xbox One S") surface as
  lower coverage / lower confidence rather than being patched locally and
  forking the model key.
- A Google Sheets adapter can later replace the workbook reader with no change
  above `adapters/`.
