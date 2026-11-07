# WinAI-OE — Adapter Resilience Specification

Status: Normative for STAGE 01 adapter work (PHASE 0027).

## 1. Adapter Rules

- Every networked adapter must guard outbound calls with a `CircuitBreaker`
  (CLOSED → OPEN after threshold failures → HALF_OPEN probe).
- `health_check()` must never raise and never log credentials; `False`
  means unreachable or unconfigured, not an exception.
- Retries use bounded exponential backoff with jitter; no unbounded loops.

## 2. Failure Semantics

- Provider outage → `health_check()` returns `False`; chat paths fall back
  per router policy instead of raising raw transport errors to the UI.
- Open circuits raise `CircuitBreakerOpenError` immediately without network
  I/O.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0027_adapter_spec.py` passes.
- No adapter embeds keys, bypasses the registry, or retries unboundedly.
