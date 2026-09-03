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
- **Score and Confidence are never multiplied.** They answer different
  questions and are reported side by side.
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

## Style

Ruff owns formatting and import order - don't hand-format. Type every function
signature. Public names are `snake_case` / `PascalCase`; a leading underscore
means "internal to this module/package".
