# WinAI-OE — Error Contract Specification

Status: Normative for STAGE 01 error work (PHASE 0072).

## 1. Exception Hierarchy

`src/orchestrator/errors.py` defines:

- `WinAIError` (base; carries `code`, `message`, `remediation`).
- `SecurityViolationError` — policy denials, traversal, forgery.
- `ToolValidationError` — schema and argument failures.
- `ProviderError` — upstream outages, rate limits, timeouts.
- `ExecutionError` — subprocess and verification failures.

## 2. Rules

- Security evaluation failures default to denial, never to silent allow.
- HTTP surfaces map invalid approvals to `404`; unknown tools to denial
  payloads with `error_code`, `message`, and `remediation`.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0072_error_spec.py` passes.
