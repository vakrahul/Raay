# WinAI-OE — Empty, Error and Retry Specification

Status: Normative for STAGE 02 retry work (PHASE 0187).

## 1. Retry Contract

- Retryable HTTP statuses: `408, 429, 500, 502, 503, 504` via
  `is_retryable_status()`; client errors (`400, 401, 403, 404`) never retry.
- `retry_with_backoff()` keeps its bound (`max_retries=3`) and jitter;
  exhaustion surfaces the last error without masking.
- Empty prompts are rejected before dispatch, never sent to providers.

## 2. Acceptance Criteria

- `tests/unit/test_phase_0187_retry_spec.py` passes.
