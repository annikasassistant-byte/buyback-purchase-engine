# 4. Scoring weights and thresholds live in config, not code

Date: 2026-09-03

## Status

Accepted

## Context

The stakeholder expects the weighting to change as the team learns from
recommendations ("sales velocity should count more than Back Market rank", "some
categories need different weighting"), and wants those changes without a code
change. The parser already uses the same pattern - business-editable rule tabs
(`EK_Regeln`, `Spiele_Titel`) read as data.

## Decision

- All business-tunable values - score weights, label thresholds, target coverage
  days, per-SKU / per-category exposure caps, velocity windows, confidence
  rules, daily budget, incoming-stock window - live in `config/engine.yml`.
- `config.py` parses it into a frozen, typed `EngineConfig` and **validates**
  it. An invalid file raises `ConfigError`; the design intent is that the caller
  keeps the last-good config rather than run on garbage.
- The shipped `engine.yml` is the default (packaged next to the code). Operators
  copy it and pass `--config` to override.
- Every run stamps a short `config_hash` onto its output. The golden test asserts
  the hash so a config change forces an intentional golden regeneration.

This mirrors the future `Purchase_Engine_Config` Google Sheet tab described in
the plan; the sheet becomes another config source behind the same loader.

## Consequences

- Weight tuning is a one-line YAML edit + a golden regenerate, reviewable on its
  own.
- No scoring constant may be hard-coded in `pipeline/`; review rejects it.
- The config surface is a compatibility contract - renaming a key is a breaking
  change and needs an ADR + CHANGELOG entry.
