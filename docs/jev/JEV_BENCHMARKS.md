# JEV Benchmarks (Measured 2026-09-21, This Workstation)

Harness: `src/orchestrator/jev/benchmark.py` over the fixed 8-task set
(`tests/unit/test_jev_benchmark.py`), mock provider, 0ms simulated
latency. Baseline = existing-system behavior (caller-supplied fallbacks).

## Results

| Metric | JEV-enabled (mock) | Existing-system baseline |
|---|---|---|
| Tasks run | 8 | 8 |
| Completion rate | 1.000 | 1.000 |
| Correctness rate | 0.625 (5/8) | 0.500 (4/8) |
| Avg latency | ~0.18 ms/decision | ~0.001 ms/decision |
| Total est. cost | ~$0.00000145 | $0.00 |
| Invalid rate | 0.000 | 0.000 |
| Fallback rate | 0.375 (3/8) | 1.000 (8/8) |

## Honest Reading

- The +1 task gap (5/8 vs 4/8) on eight toy tasks proves the plumbing
  works — routing, validation, fallback accounting — **not** that JEV is
  better. No superiority claim is made.
- Fallback rate 0.375 shows the safety net engaging on low-confidence
  calls, exactly as designed.
- Costs are negligible at mock scale; real-provider economics require
  re-measurement against live tariffed traffic.

## Limitations / Do-Not-Use Cases

- Do not use JEV outputs as authorizations (security gateway enforced).
- Do not use for open-ended generation — JEV-style models decide, LLMs
  generate; keep each layer in its lane.
- Do not benchmark with the mock and report it as model quality.
