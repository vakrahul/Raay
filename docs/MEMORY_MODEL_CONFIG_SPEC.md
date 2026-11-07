# WinAI-OE — Memory and Model Configuration Specification

Status: Normative for STAGE 02 memory/model work (PHASE 0142).

## 1. Memory Controls Contract

- Views must offer inspect, correct, export, and delete over every tier.
- `memory_stats()` reports per-tier counts for cards without exposing
  content; full content loads only on explicit user drill-down.

## 2. Model Configuration Contract

- Views list provider id, model, context window, tool/streaming flags,
  active marker, and local-vs-cloud egress disclosure.
- Routing preferences and task budgets edit only through validated forms.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0142_memory_model_spec.py` passes.
