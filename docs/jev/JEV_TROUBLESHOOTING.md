# JEV Troubleshooting

## Decisions always fall back

- Check `GET /api/v1/jev/status`: `enabled`, `healthy`, `budget_remaining`.
- Common causes: router disabled, provider unhealthy, confidence below
  threshold, request budget exhausted, activation policy disallowing the
  kind. Each fallback carries a `reason` — read it before changing config.

## Timeouts on every call

- Raise `timeout_ms` (default 500) or check provider latency in
  `/api/v1/jev/metrics`. Persistent timeouts mean the provider path is
  unusable; the system is designed to run fully on fallbacks.

## "Mock result" warnings

- Expected while `mock-jev` serves traffic. To use real JEV: obtain
  TypeSafe AI early-access credentials, implement `BaseJevProvider` with
  `is_mock=False`, and re-run Stages 10–11 against live traffic.

## Approval still required after JEV recommendation

- Correct behavior. JEV never authorizes; `REQUIRE_APPROVAL` means the
  human pipeline is doing its job. Approve or deny in the UI as usual.

## Removing JEV safely

See `JEV_CONFIGURATION.md` (Safe Removal). The application, all 82+
pre-existing tests, and every existing workflow run identically without it.
