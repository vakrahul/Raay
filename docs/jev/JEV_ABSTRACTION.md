# JEV Provider Abstraction (Stage 2)

## Modules

- `src/orchestrator/jev/base.py` — `JevDecisionKind` (7 kinds),
  `JevDecisionRequest` / `JevDecisionResponse` (strict Pydantic, extra
  fields forbidden), `BaseJevProvider` ABC, `JevError` hierarchy
  (`JevTimeoutError`, `JevUnavailableError`, `JevValidationError`),
  `validate_jev_response()`, and `decide_with_timeout()`.
- `src/orchestrator/jev/mock_adapter.py` — `MockJevAdapter`
  (`provider_name="mock-jev"`, `is_mock=True` always). Deterministic
  keyword/overlap heuristics with MD5 tie-breaks (never `hash()`), plus
  configurable simulated latency for timeout testing.

## Key Guarantees

- Requests carry unique IDs, candidate lists (1–255 unique non-empty
  strings), bounded context (1–8000 chars), and per-request timeouts.
- Responses are validated: request-ID echo, kind echo, candidate
  membership, probabilities summing to ~1.0.
- Timeouts raise `JevTimeoutError` for deterministic fallback; cancellation
  propagates as `asyncio.CancelledError`.
- No credentials anywhere in the layer; no network calls in the mock.

## Mock vs Real

The mock demonstrates the interface only. It performs no calibrated
inference and must never be presented as real JEV output. A verified
TypeSafe AI provider would implement `BaseJevProvider` with
`is_mock=False` and real API transport.
