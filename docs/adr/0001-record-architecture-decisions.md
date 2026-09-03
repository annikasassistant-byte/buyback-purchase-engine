# 1. Record architecture decisions

Date: 2026-09-03

## Status

Accepted

## Context

The Purchase Engine encodes a number of non-obvious choices that come straight
from stakeholder conversations and the Technical Implementation Plan (e.g. "score
and confidence are never multiplied", "stock is unknown, not zero"). Without a
record, a future contributor will "fix" one of these and quietly break the
design intent.

## Decision

We keep lightweight [Architecture Decision Records](https://adr.github.io/) in
`docs/adr/`, numbered and in Michael Nygard's format (Context / Decision /
Consequences). One ADR per decision, roughly one page. This file is both the
first record and the template.

An ADR is warranted when a change touches the layering, a scoring or quantity
formula, a data contract (a port or the workbook schema we depend on), or the
persistence model. Small refactors and bug fixes do not need one.

ADRs are immutable once Accepted. A later decision that reverses an earlier one
gets its own ADR and flips the old one's status to `Superseded by NNNN`.

## Consequences

- Every PR that changes design intent carries a short, reviewable rationale.
- Newcomers can read `docs/adr/` in order to understand *why* the code looks the
  way it does.
- Minor overhead per significant decision; none for routine work.
