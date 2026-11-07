# WinAI-OE — Dependency Reproducibility Specification

Status: Normative for STAGE 01 reproducibility work (PHASE 0092).

## 1. Mirror Rule

Every runtime family in `pyproject.toml [project].dependencies` must appear
in `requirements.txt` with an equivalent lower-bound floor, and vice versa.
Drift in either direction is a violation.

## 2. Rules

- Family comparison is case-insensitive on normalized names (`-`, `_`, `.`
  equivalent; extras stripped).
- Version specifiers may differ in exact bounds, but the family set must be
  identical.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0092_repro_spec.py` passes.
