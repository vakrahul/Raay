# JEV Memory and Context Integration (Stage 7)

## Context Packer

`src/orchestrator/jev/decision_context.py`:

- `pack_decision_context()` — truncates descriptions (default 500 chars),
  redacts secret patterns via the audit-log scrubber, and drops every
  caller-supplied field outside the allowlist
  (`task_description`, `task_type`, `candidates`). Explicit arguments
  always win over extras, so injected overrides are impossible.
- `filter_allowed_fields()` — drops sensitive keys (keys, passwords,
  tokens, cookies, credentials) and unlisted keys (directories, file
  contents, unrelated memories).

## Separation Guarantee

- `JevDecisionLog` is bounded (default 200 entries) and in-memory only:
  process lifetime, no SQLite, no merge into `BrainSubsystem` stores.
- Inferred preferences are never promoted to permanent facts; memory
  policies (`VERIFIED_FACT` vs `MODEL_HYPOTHESIS`) continue to govern.
