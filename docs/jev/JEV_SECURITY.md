# JEV Security Integration (Stage 6)

## Gateway

`src/orchestrator/jev/security_gateway.py` — `JevSecurityGateway` over
`SecurityPolicyEngine` + `DynamicDefenseGuard` + `ActionValidator`.

Every JEV-derived tool call passes three gates in order:

1. **Adversarial screen** — heuristic injection scan plus explicit
   shell-escape detection inside argument structures → DENY (CRITICAL).
2. **Schema validation** — strict Pydantic/action schemas → DENY (HIGH).
3. **Policy evaluation** — ALLOW / DENY / REQUIRE_APPROVAL with
   single-use approval nonces; forged or replayed nonces → DENY.

## Non-Negotiable Rules

- JEV recommendations confer zero trust; `jev_source` is provenance
  metadata only and never influences a decision.
- The gateway grants nothing: agent picks must name registered agents,
  model picks must stay in the allowlist (see `validate_agent_pick`,
  `validate_model_pick`).
- Approval claims inside JEV text are worthless — only a consumed nonce
  authorizes, and only for its bound action.
