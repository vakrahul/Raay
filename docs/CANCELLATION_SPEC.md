# WinAI-OE — Cancellation Specification

Status: Normative for STAGE 02 cancellation work (PHASE 0162).

## 1. Cancel Contract

- Cancelling twice is safe: every cancel request receives exactly one
  `{event: cancelled}` frame and leaves no dangling generation state.
- `build_cancelled_event()` is the single constructor for the frame.
- Client-side cancellation propagates through the generation token;
  server-side collection propagates `asyncio.CancelledError`.

## 2. Acceptance Criteria

- `tests/unit/test_phase_0162_cancel_spec.py` passes.
