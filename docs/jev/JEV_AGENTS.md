# JEV Agent Orchestration Integration (Stage 4)

## Advisor

`src/orchestrator/jev/agent_advisor.py` — `JevAgentAdvisor` over the
existing `AgentRegistry` and `DynamicAgentFactory`.

- `recommend()` — selects one **already-registered** agent, optionally
  weighs reusing an existing agent, optionally assesses escalation, and
  always attaches the agent's tool allowlist for downstream enforcement.
- `recommend_split()` — boolean subtask-split recommendation.
- Unknown candidates are filtered before dispatch; an unregistered
  fallback raises `JevValidationError`; a full agent pool forces
  `escalate=True`.

## Hard Limits (Never Bypassed)

- Read-only: the advisor creates no agents and changes no permissions.
  Spawning stays inside `DynamicAgentFactory` (depth and pool caps).
- Every recommendation carries `source` (`jev`/`fallback`), confidence,
  and rationale; escalation outputs authorize nothing — the
  human-approval pipeline remains the sole authorizer.
