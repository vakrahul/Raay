# JEV Configuration

## Runtime Controls

- **Enable/disable:** dashboard JEV card toggle, or
  `POST /api/v1/jev/config {"enabled": false}`. Disabling stops all
  provider traffic immediately; the rest of the application is unaffected.
- **Confidence threshold:** `JevRouterConfig(confidence_threshold=…)` —
  below-threshold decisions fall back (default 0.55).
- **Timeouts:** per-request `timeout_ms` (default 500); service default
  500ms.
- **Budgets:** `JevRequestBudget(max_decisions=…)` caps calls per scope;
  exhaustion forces fallback without provider traffic.
- **Activation policy:** `JevActivationPolicy(enabled, allowed_kinds)` —
  disabled kinds never reach a provider.

## Provider Setup

- Default provider is `mock-jev` (offline simulator). A verified TypeSafe
  AI provider implements `BaseJevProvider` with `is_mock=False`; no
  credentials are hard-coded anywhere — inject them at construction time.
- No environment variables are required by the JEV layer itself.

## Safe Removal

Delete `src/orchestrator/jev/`, its tests, the `/api/v1/jev/*` routes in
`src/orchestrator/main.py`, and the dashboard JEV card. All existing
suites pass without it (verified in Stage 10).
