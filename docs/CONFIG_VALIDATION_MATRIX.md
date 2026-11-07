# WinAI-OE — Configuration Validation Matrix

Status: Normative for STAGE 01 validation work (PHASE 0057).

| Field | Rule | Failure Mode |
|---|---|---|
| `host` | Allowlist `{127.0.0.1, localhost, ::1}` | `ValidationError` (security) |
| `port` | Integer within `1024–65535` | `ValidationError` |
| `workspace_root`, `data_dir`, `logs_dir` | Canonicalized absolute paths | Auto-resolved |
| `subprocess_timeout_seconds` | `1–600` | `ValidationError` |
| `subprocess_max_memory_mb` | `128–8192` | `ValidationError` |
| `subprocess_max_output_bytes` | `1024–1048576` | `ValidationError` |
| `context_window_tokens` | `>= 1024` | `ValidationError` |

## Acceptance Criteria

- `tests/unit/test_phase_0057_validation_matrix.py` passes.
- No validation path logs secrets or bypasses the allowlist.
