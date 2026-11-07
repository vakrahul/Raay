# WinAI-OE — Brain Memory Specification

Status: Normative for STAGE 01 brain work (PHASE 0032).

## 1. Tier Contracts

- Working: ephemeral session goals, observations, variables; cleared on exit.
- Persistent (SQLite WAL): episodic summaries surviving restarts.
- Semantic: vector-indexed facts with cosine retrieval; local embeddings only.
- Project: workspace-scoped notes bound to the authorized root directory.
- Error reflections: failure traces with root cause plus verified correction.

## 2. Provenance Rules

- `VERIFIED_FACT` only from tool-verified evidence or passing tests.
- `MODEL_HYPOTHESIS` must never persist as fact without verification.
- `FAILED_APPROACH` entries are excluded from general retrieval.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0032_brain_spec.py` passes.
- No tier reads outside the workspace root or emits secrets.
