# WinAI-OE — Context Windowing Specification

Status: Normative for STAGE 02 windowing work (PHASE 0192).

## 1. Window Contract

- `window_messages()` keeps all `system` messages (security head) plus the
  newest non-system turns up to `max_messages` total.
- `max_messages <= 0` yields an empty list; the system head is never
  trimmed to satisfy the budget.

## 2. Acceptance Criteria

- `tests/unit/test_phase_0192_windowing_spec.py` passes.
