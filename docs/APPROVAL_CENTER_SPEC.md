# WinAI-OE — Approval Center Specification

Status: Normative for STAGE 02 approval work (PHASE 0137).

## 1. Approval Contract

- Every sensitive action displays immutable parameters (tool, target,
  risk tier, reason) before the user decides.
- Approval completes only with the exact single-use nonce bound to the
  action hash; forged, expired, replayed, or mismatched nonces are denied.
- Deny is the default; silence or dismissal never authorizes.

## 2. Acceptance Criteria

- `tests/unit/test_phase_0137_approval_spec.py` passes.
