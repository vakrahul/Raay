# WinAI-OE — WebSocket Streaming Specification

Status: Normative for STAGE 01 streaming work (PHASE 0082).

## 1. Event Vocabulary

Outbound events: `token`, `tool_proposal`, `done`, `cancelled`.
Inbound actions: `chat` (with `messages`), `cancel`.

## 2. Rules

- Every `token` carries a string chunk; `tool_proposal` carries the tool
  call plus its policy decision; `done` terminates the turn.
- Unknown inbound actions receive no state change and must not crash the
  connection.
- Disconnects are caught and logged without credential exposure.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0082_ws_spec.py` passes.
