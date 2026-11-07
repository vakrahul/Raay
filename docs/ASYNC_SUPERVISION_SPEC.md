# WinAI-OE — Async Supervision Specification

Status: Normative for STAGE 01 supervision work (PHASE 0077).

## 1. Execution Rules

- Every subtask runs under `asyncio.wait_for` bounded by its declared
  `max_duration_seconds`; timeouts mark the subtask FAILED and halt the plan.
- Empty executor results are rejected as verification failures.
- `cancel_plan()` transitions all PENDING/IN_PROGRESS work to CANCELLED and
  records a checkpoint; it never deletes history.

## 2. Acceptance Criteria

- `tests/unit/test_phase_0077_supervision_spec.py` passes.
