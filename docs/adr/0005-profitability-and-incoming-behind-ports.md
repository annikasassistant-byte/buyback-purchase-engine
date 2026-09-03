# 5. Profitability and incoming-stock sit behind ports

Date: 2026-09-03

## Status

Accepted

## Context

Two inputs are known to be temporary:

- **Profitability.** The MVP uses a trailing-window margin proxy on `Status=OK`
  sales rows plus the parser's "average of the last N purchases" EK method. A
  separate Profit Engine project will replace it.
- **Incoming stock** ("bought, not yet sellable"). The MVP uses a rolling-window
  proxy off `EK_Normalisiert.Kaufdatum`. The real source is a hand-entered
  purchasing Google Sheet that first needs an adapter through the parser's
  purchase-side text pipeline, plus a one-time backfill.

Both will change; the scoring code around them should not.

## Decision

Define them as `typing.Protocol` ports in `domain/ports.py`:

- `Profitability.get_profitability(pid, as_of) -> ProductProfitability` - a fixed
  6-field record with a fixed status vocabulary (`CONFIRMED` / `TEMP_CALCULATED`
  / `PRÜFEN` / `UNAVAILABLE`). Scoring reads those fields and **never branches on
  `source`**.
- `IncomingStockSource.counts(as_of) -> IncomingCounts` - per-model-key
  `purchased_today` and `older_incoming`, kept as separate terms.

MVP adapters: `TrailingWindowProfitability`, `EkNormalisiertIncoming`. The
replacements (`ProfitEngineClient`, `LivePurchaseTableIncoming`) implement the
same port; `config.incoming.source` selects the incoming adapter.
`LivePurchaseTableIncoming` exists as a stub that raises with guidance, so the
seam is visible in the code, not just the docs.

## Consequences

- Swapping either input is an adapter change plus a config flag - `pipeline/`
  does not change, and its unit tests (which inject fakes) keep passing.
- The 6-field profitability shape is a contract; adding a field is a
  non-breaking change, changing the status vocabulary is breaking (ADR).
- `ProductProfitability.__post_init__` rejects an unknown status, so a
  mis-implemented adapter fails loudly.
