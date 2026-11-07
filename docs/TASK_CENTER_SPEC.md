# WinAI-OE — Task Center Specification

Status: Normative for STAGE 02 task-center work (PHASE 0122).

## 1. Progress Contract

- `TaskPlan.progress()` returns `(completed, total, percent)` where
  `completed` counts `COMPLETED` subtasks; empty plans report `(0, 0, 0.0)`.
- Center buckets: active (`IN_PROGRESS`), queued (`PENDING`),
  completed, failed, pending-approval (`AWAITING_APPROVAL`).

## 2. Rules

- Progress never divides by zero and never exceeds 100%.
- Emergency stop surfaces through `IsEmergencyStopped`, never by hiding
  running tasks.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0122_taskcenter_spec.py` passes.
