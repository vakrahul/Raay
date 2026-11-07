# JEV Architecture

JEV (TypeSafe AI System One decision model) integrates into WinAI-OE as
an **advisory fast-decision layer** in front of — never inside — the
existing orchestration, security, and execution systems.

## Module Map (`src/orchestrator/jev/`)

| Module | Role |
|---|---|
| `base.py` | Provider-neutral contracts: `JevDecisionKind`, request/response schemas, `BaseJevProvider`, error hierarchy, timeout + validation helpers |
| `mock_adapter.py` | Deterministic offline simulator (`mock-jev`, `is_mock=True`); NOT real JEV |
| `router.py` | `JevDecisionRouter`: 8 use cases with mandatory fallbacks, confidence threshold, per-use-case metrics |
| `agent_advisor.py` | Read-only agent/split/escalation recommendations over the existing registry |
| `model_advisor.py` | Allowlist- and privacy-constrained model routing beside `IntelligentRouter` |
| `security_gateway.py` | Three-gate authorization (screen → schema → policy) for JEV-derived actions |
| `decision_context.py` | Minimized, redacted context packs plus ephemeral decision log |
| `usage_tracker.py` | Per-provider usage, budgets, activation policy, tariff-based costing |
| `service.py` | Runtime owner: config, status, metrics, bounded test decisions |
| `benchmark.py` | Measured JEV-vs-baseline comparison harness |

## Data Flow

```
Task → [JEV fast advice: classify/route/select] → [Security gateway]
     → [Existing orchestrator executes] → [Audit log + metrics]
                 ↑ fallback on any JEV failure
```

JEV output is untrusted input at every boundary. The security policy
engine, approval broker, and human authorizations are unchanged and
retain absolute veto.
