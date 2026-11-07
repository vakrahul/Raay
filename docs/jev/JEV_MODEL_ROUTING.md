# JEV Model Router Integration (Stage 5)

## Advisor

`src/orchestrator/jev/model_advisor.py` — `JevModelAdvisor` alongside the
existing `IntelligentRouter` (never replacing it).

- `recommend()` — suggests one provider ID strictly inside the
  caller-supplied allowlist; validates registration, tool capability, and
  privacy constraints; records a human-readable rationale.
- Privacy mode restricts candidates and fallback to local/offline
  providers and truncates decision context (`minimize_context`), so
  private project data never reaches a new provider without authorization.
- Optional `use_legacy_fallback` consults the existing router's pick when
  it stays inside the allowlist; otherwise the caller default applies.

## Guarantees

- Disallowed, unregistered, or non-local-under-privacy selections are
  impossible: violations raise `JevValidationError` or fall back closed.
- Every outcome carries `source`, confidence, rationale, and
  `privacy_enforced`; metrics flow through the shared router metrics.
- No cost or accuracy claims are made; benchmarking belongs to Stage 11.
