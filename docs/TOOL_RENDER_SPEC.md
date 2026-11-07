# WinAI-OE — Tool Rendering Specification

Status: Normative for STAGE 02 rendering work (PHASE 0177).

## 1. Rendering Contract

- `render_tool_summary()` produces one redacted line:
  `tool_name` plus truncated `key=value` pairs; secret values become
  `[REDACTED]`; output caps at 200 characters.
- Summaries never include raw file contents, credentials, or full paths
  outside the workspace root.

## 2. Acceptance Criteria

- `tests/unit/test_phase_0177_render_spec.py` passes.
