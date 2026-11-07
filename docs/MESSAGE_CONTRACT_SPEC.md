# WinAI-OE — Message Contract Specification

Status: Normative for STAGE 02 message work (PHASE 0152).

## 1. Shape Contract

- Roles are exactly `system|user|assistant|tool`; tool messages should
  carry `tool_call_id`.
- Tool proposals bind `id`, `tool_name`, and a JSON-safe `arguments` dict.
- Responses expose `has_tool_calls` for dispatch branching; empty tool
  lists mean plain conversational replies.

## 2. Acceptance Criteria

- `tests/unit/test_phase_0152_message_spec.py` passes.
