# WinAI-OE — Conversation History Specification

Status: Normative for STAGE 02 history work (PHASE 0167).

## 1. History Contract

- Observations append with tool provenance and verification status.
- `recent_observations(limit)` returns the newest entries first, capped
  at `limit`; non-positive limits yield an empty list.
- Summaries show at most the 5 newest observations; `clear()` resets all
  session state.

## 2. Acceptance Criteria

- `tests/unit/test_phase_0167_history_spec.py` passes.
