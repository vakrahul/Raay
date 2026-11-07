# WinAI-OE — Persistence Specification

Status: Normative for STAGE 02 persistence work (PHASE 0197).

## 1. Durability Contract

- SQLite runs in WAL mode; every mutation commits before returning.
- `count_by_type()` reports per-type record counts without loading content.
- Restarting the process against the same database file restores all
  records byte-identical.

## 2. Acceptance Criteria

- `tests/unit/test_phase_0197_persist_spec.py` passes.
