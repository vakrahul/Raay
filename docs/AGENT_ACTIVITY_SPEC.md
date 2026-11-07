# WinAI-OE — Agent Activity Specification

Status: Normative for STAGE 02 activity work (PHASE 0117).

## 1. Status Vocabulary

Canonical panel states: `Idle`, `Planning`, `ExecutingTool`,
`AwaitingApproval`, `Completed`, `Failed`, `Terminated`.
Python `ExecutionStage` mapping: PLANNING→Planning, EXECUTING→ExecutingTool,
WAITING_FOR_APPROVAL→AwaitingApproval, COMPLETED→Completed, FAILED→Failed,
CANCELLED→Terminated; VERIFYING/RECOVERING/PARTIALLY_COMPLETED display as
ExecutingTool with detail text.

## 2. Rules

- Terminal states (`Completed`, `Failed`, `Terminated`) are immutable once
  reported; only a new task assignment returns an agent to `Idle`.
- `LastUpdated` is always UTC; activity older than the task timeout is
  shown as stale, never silently dropped.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0117_activity_spec.py` passes.
