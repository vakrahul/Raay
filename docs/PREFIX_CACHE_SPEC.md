# WinAI-OE — Prefix Cache Specification

Status: Normative for STAGE 02 prefix work (PHASE 0172).

## 1. Cache Contract

- Static system instructions and tool schemas lead every prompt so
  provider prefix caches hit; dynamic content trails.
- Exact hits return cached responses with zero new tokens; semantic hits
  require similarity at or above threshold.
- `invalidate_model()` purges all entries for a rotated or retired model.

## 2. Acceptance Criteria

- `tests/unit/test_phase_0172_prefix_spec.py` passes.
