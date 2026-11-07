# WinAI-OE — Health Endpoint Specification

Status: Normative for STAGE 01 health work (PHASE 0062).

## 1. Payload Contract

`GET /health` returns exactly:

- `status`: `healthy` when the active provider reports healthy, else
  `degraded`. Never `500` for provider outages.
- `app`, `version`, `environment`: plain service metadata.
- `provider`: active provider name only (no keys or tokens).
- `provider_healthy`: boolean.
- `configured_providers`: capability metadata (ids, models, flags).

## 2. Rules

- Provider exceptions inside health probing must degrade, never propagate
  tracebacks or credentials.
- Health checks must complete quickly and never trigger billable inference.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0062_health_spec.py` passes.
