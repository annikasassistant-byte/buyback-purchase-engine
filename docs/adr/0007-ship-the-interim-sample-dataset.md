# 7. Ship the interim sample dataset inside the repository

Date: 2026-09-03

## Status

Accepted

## Context

The MVP's only input is one workbook export: `BuyBack - Profit (Aktualisiert
2026-09-02).xlsx`. There is currently no live connection to the parser's output
(no Google Sheets API access wired up, no JTL API) - see the "Improve later"
column throughout the plan and `docs/architecture.md`'s "What is deliberately
not here". Until that lands, this static export **is** the data the engine
runs on; there is no other source to point at.

That file contains real business data: purchase prices, margins, and - in
`EK_Normalisiert` - private sellers' names and postal codes
(`Verkäufername`, `PLZ / Ort / Land`). It was originally kept out of git
entirely (`.gitignore`d) for exactly that reason.

Keeping it out of the repo has a real cost while it's the *only* data source:
nobody can clone the repo and run it, the CLI's "auto-discover the workbook"
behaviour is untestable from a fresh checkout, and the golden test - the one
regression check that runs the real pipeline end to end - can only ever run on
a machine that happens to already have a copy of this exact file placed by
hand. For a small, private, contractor-maintained repo at this stage, that
outweighs the benefit of keeping it out of version control.

## Decision

- Track this **one specific file** in git, at
  `data/raw/full_dataset_2026_run/BuyBack - Profit (Aktualisiert 2026-09-02).xlsx`,
  as a versioned **sample fixture** - explicitly the interim dataset, not a
  live feed, and not a statement that data files in general are fine to
  commit here.
- `.gitignore` allow-lists exactly this path (`/data/raw/*` stays ignored,
  with a narrow `!` exception for this one file). A fresh export dropped into
  `data/raw/` tomorrow - the daily JTL snapshot, the live purchase table -
  stays ignored by default; it does not become trackable just by living next
  to the sample.
- When the file is replaced by a newer dated export, the old one is removed
  and the new one added in the same commit (not both kept), and the golden
  fixture (`tests/golden/data/summary.json`) is regenerated alongside it.
- This is a **local git decision**, not a publication decision: whether and
  when this history is pushed to the GitHub remote is judged separately, each
  time, against who has access to that remote at that point.

## Consequences

- `pytest` runs the golden test from a plain `git clone` with no manual setup
  - the actual regression check that matters most now runs by default.
- The CLI's default-workbook auto-discovery works out of the box for anyone
  with the repo.
- The repository's git history now contains real seller PII. Before this
  history is ever pushed anywhere with broader access than it has today - a
  public repo, a wider team, a different hosting account - that decision needs
  to be revisited explicitly; this ADR does not authorise that on its own.
- This is explicitly temporary. Once Phase 2/3 lands a live connection
  (Google Sheets API or the JTL API - see `docs/architecture.md`), this ADR
  should be superseded: stop shipping a static sample, and either drop the
  committed file or replace it with a small synthetic/sanitised fixture for
  CI, keeping the real feed live-only.
