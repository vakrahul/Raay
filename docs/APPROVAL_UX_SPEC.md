# WinAI-OE — Approval UX Specification

Status: Normative for STAGE 02 approval-UX work (PHASE 0182).

## 1. Display Contract

- The dialog shows tool, target, risk tier, and reason as read-only text.
- `describe_request()` mirrors the same four fields for Python logs and
  dashboards; the nonce and parameters never appear in display text.
- Deny is the default button; dismissal equals denial.

## 2. Acceptance Criteria

- `tests/unit/test_phase_0182_approval_ux_spec.py` passes.
