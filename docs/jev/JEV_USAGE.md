# JEV Cost and Performance Management (Stage 8)

## Tracking

`src/orchestrator/jev/usage_tracker.py` — `JevUsageTracker` records per
provider: requests, successes, failures, timeouts, fallbacks, overrides,
validation failures, latency, and cost. `summary()` exposes success,
fallback, and availability rates plus average latency and spend.

## Budgets and Activation

- `JevRequestBudget` — per-scope decision cap; `try_consume() == False`
  means no provider call may be made (fallback path instead).
- `JevActivationPolicy` — global enable flag plus per-kind allowlist;
  disabled or disallowed kinds never reach a provider.

## Cost Model

`estimate_jev_cost_usd()` applies the published tariff ($0.042/M input,
$0.00 output) to counted characters (~4 chars/token). All savings or
spend figures in dashboards must come from these measured counters —
never from estimates presented as facts.
