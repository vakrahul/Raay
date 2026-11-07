# WinAI-OE — Chat Workspace Specification

Status: Normative for STAGE 02 chat work (PHASE 0112).

## 1. Interaction Contract

- Send appends a `user` message plus an `assistant` placeholder with
  `IsStreaming=true`; completion replaces the placeholder content.
- `IsGenerating` gates duplicate sends; Cancel triggers the generation
  `CancellationTokenSource`.
- Roles are restricted to `system|user|assistant|tool` on both the C# and
  Python sides; anything else is rejected before dispatch.

## 2. Acceptance Criteria

- `tests/unit/test_phase_0112_chat_spec.py` passes.
