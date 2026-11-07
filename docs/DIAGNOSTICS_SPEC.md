# WinAI-OE — Diagnostics Specification

Status: Normative for STAGE 01 diagnostics work (PHASE 0097).

## 1. Command Contract

`python scripts/diagnostics.py` prints a single human-readable report with:

- Python version and key package versions (fastapi, pydantic, httpx).
- Registered HTTP route inventory from a testing-config app instance.
- Results of the offline checkers (layout, packaging, drift, inventory).

## 2. Rules

- Diagnostics output must never contain secrets, keys, or tokens.
- Exit code 0 means all checks passed; 1 lists violations.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0097_diagnostics_spec.py` passes.
