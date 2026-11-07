# JEV User Interface (Stage 9)

## Dashboard Panel

The existing dashboard (`GET /dashboard`) gained a JEV card showing
enable/disable toggle, provider name, connection health, capability and
verification status, latency/usage metrics, recent decisions, a
test-decision button, and an always-visible MOCK MODE badge while the
mock adapter serves traffic. Disabling JEV takes effect immediately;
the panel degrades to "unreachable" text if the service is slow.

## Endpoints

- `GET /api/v1/jev/status` — enabled flag, provider, mock warning,
  health, capabilities, verification, budget, timeout.
- `GET /api/v1/jev/metrics` — usage and router metrics.
- `GET /api/v1/jev/decisions?limit=` — recent decision history.
- `POST /api/v1/jev/config {"enabled": bool}` — immediate toggle.
- `POST /api/v1/jev/test {"context": str}` — one bounded decision;
  mock results are explicitly labelled and never shown as real.

No confidence values or explanations are fabricated: the panel renders
only measured confidence, latency, and spend from the tracker.
