# Contributing

Internal BuyBack project. This file is the working agreement for anyone touching
the code.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
make install          # editable install + dev tools + pre-commit hooks
```

`uv` works too: `uv sync --extra dev`.

## Day-to-day

| Task | Command |
|---|---|
| Lint | `make lint` |
| Auto-fix + format | `make format` |
| Type-check | `make typecheck` |
| Tests + coverage | `make test` |
| Everything CI runs | `make check` |
| Sample engine run | `make run BUDGET=1500` |

`make check` must pass before a PR. Pre-commit runs Ruff (+ `--fix`), Ruff
format and mypy on every commit.

## Architecture rules (enforced by review)

The layering is **domain ← adapters ← pipeline ← cli**; imports only ever point
inward. See [`docs/architecture.md`](docs/architecture.md).

- **Read-only on the parser.** The engine never re-parses a product name and
  never writes back to the parser's sheets. All new logic lives here.
- **Score and Confidence are never multiplied or combined into one number.**
  They answer different questions and are reported side by side. Confidence
  *is* allowed to read which Purchase Score components had data for a product
  (ADR 0008) - that's still two separate numbers, not one derived from the
  other's value.
- **Missing score components are redistributed, never zero-filled.** Uncertainty
  belongs in the Confidence Score, never as a Purchase Score penalty.
- **Business knobs live in `config/engine.yml`**, not in code. If you find
  yourself hard-coding a weight or threshold, move it to config.
- **New external data source?** Implement the relevant port in `domain/ports.py`
  under `adapters/`; do not reach into the pipeline.

## Decisions

Anything that changes the architecture, a scoring formula, or a data contract
gets an ADR in [`docs/adr/`](docs/adr/) (copy `0001` as the template) and a
`CHANGELOG.md` entry. Regenerate the golden fixture in the same PR:

```bash
make golden      # PE_WRITE_GOLDEN=1 pytest tests/golden --no-cov -q
```

## Data

The engine's only input, `data/raw/full_dataset_2026_run/BuyBack - Profit
(Aktualisiert 2026-09-02).xlsx`, is checked into the repo as an interim sample
fixture — see [ADR&nbsp;0007](docs/adr/0007-ship-the-interim-sample-dataset.md)
for why. It carries real private-seller data, so it's the **only** thing
`.gitignore` allow-lists under `data/raw/` — anything else you drop in there
(a fresher export, the live purchase table, a JTL CSV) stays ignored by
default; don't widen that allow-list without re-reading the ADR first.

Replacing it with a newer export: delete the old file and add the new one in
the same commit (never both), update the filename everywhere it's still
literal (`DEFAULT_WORKBOOK_GLOB` in `adapters/workbook.py` is a glob so it
doesn't need touching; the golden test docstring and this file's date do), and
regenerate the golden fixture in that commit.

Whether and when this repository's history — which now contains that PII — is
pushed anywhere, or who gets access to the remote, is a decision to make
deliberately each time, not an assumption to carry forward from the last time.

## Style

Ruff owns formatting and import order - don't hand-format. Type every function
signature. Public names are `snake_case` / `PascalCase`; a leading underscore
means "internal to this module/package".
