# JEV Testing

Run: `python -m pytest tests/unit/test_jev_abstraction.py
tests/unit/test_jev_router.py tests/unit/test_jev_agents.py
tests/unit/test_jev_model_routing.py tests/unit/test_jev_security.py
tests/unit/test_jev_context.py tests/unit/test_jev_usage.py
tests/unit/test_jev_ui.py tests/unit/test_jev_benchmark.py
tests/unit/test_jev_stage10.py -q`

## Coverage Map

| Area | File | Tests |
|---|---|---|
| Abstraction | test_jev_abstraction.py | 11 — validation, mock labelling, guardrail/model direction, timeout, malformed rejection, cancellation, health |
| Router | test_jev_router.py | 11 — 8 use cases, disabled/timeout/low-confidence/invalid/unhealthy/lying fallbacks, metrics |
| Agents | test_jev_agents.py | 7 — registration checks, filtering, reuse, split, escalation, capacity gate, timeout |
| Model routing | test_jev_model_routing.py | 8 — allowlist, privacy firewall, fallback rejection, timeout, minimization, legacy hook, tools |
| Security | test_jev_security.py | 12 — allow/deny/injection/escape/escalation/malformed/unknown/approval/nonce/agent-model/auth-claim |
| Context | test_jev_context.py | 6 — truncation, redaction, filtering, override-smuggling, log bounds, separation |
| Usage | test_jev_usage.py | 6 — cost math, rates, budgets, activation, measured comparison |
| UI/service | test_jev_ui.py | 5 — status, toggle, test decision, panel markup, endpoint round-trip |
| Benchmark | test_jev_benchmark.py | 4 — task set, measured rates, baseline comparison, empty suite |
| Stage 10 gaps | test_jev_stage10.py | 6 — config rejection, disabled-app regression, crash-proofing, injection, approval, disabled service |

Full suite (`python -m pytest tests -q`) passes with zero failures;
JEV suites are additive and the pre-existing baseline is untouched.
