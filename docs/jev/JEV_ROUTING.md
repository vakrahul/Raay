# JEV Decision Routing (Stage 3)

## Router

`src/orchestrator/jev/router.py` — `JevDecisionRouter` over any
`BaseJevProvider`, governed by `JevRouterConfig` (`enabled`,
`confidence_threshold`, `timeout_ms`, `check_health_first`).

## Supported Use Cases

| Method | Kind | Candidates | Fallback |
|---|---|---|---|
| `classify_task` | task_classification | simple/moderate/complex | moderate |
| `select_agent` | agent_selection | caller-supplied roles | caller default |
| `select_model` | model_selection | caller-supplied models | caller default |
| `select_tool` | tool_selection | caller-supplied tools | caller default |
| `route_workflow` | workflow_branch | caller-supplied branches | caller default |
| `decide_retry` | workflow_branch | retry/fallback/abort | abort |
| `assess_escalation` | guardrail_assessment | proceed/escalate | escalate |
| `plan_cost_aware` | model_selection | cheap/balanced/frontier | balanced |

## Fallback Contract

Fallback triggers on: router disabled, unhealthy provider, timeout,
validation failure, confidence below threshold, or invalid fallback
value. Every fallback records `source="fallback"` plus a reason in
per-use-case metrics (`requests`, `jev_selected`, `fallbacks`,
`timeouts`, `validation_failures`, `avg_latency_ms`).

JEV stays advisory: escalation outputs authorize nothing, and ordinary
conversations run with the router disabled by default.
