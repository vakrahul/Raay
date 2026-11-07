# WinAI-OE — Test Health Specification

Status: Normative for STAGE 01 test work (PHASE 0047).

## 1. Suite Contracts

- `tests/unit/`: deterministic, no network, no GUI; per-phase files named
  `test_phase_NNNN_<slug>.py`.
- `tests/integration/`: TestClient-based API/WebSocket flows plus real
  filesystem/subprocess behavior inside temporary workspaces.
- `tests/security/`: adversarial traversal, injection, forgery, and
  isolation checks; must fail closed on any bypass.

## 2. Health Gates

- Full suite `python -m pytest tests -q` passes with zero failures before
  any phase sign-off.
- New behavior ships with new tests; security work ships with adversarial
  tests.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0047_test_spec.py` passes.
