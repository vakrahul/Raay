# WinAI-OE — Streaming Specification

Status: Normative for STAGE 02 streaming work (PHASE 0157).

## 1. Chunk Contract

- Streams yield non-empty string chunks in order; consumers concatenate.
- `collect_stream()` truncates at `max_chars` (default 50,000) to bound
  memory; cancellation propagates as `asyncio.CancelledError`.

## 2. Acceptance Criteria

- `tests/unit/test_phase_0157_streaming_spec.py` passes.
