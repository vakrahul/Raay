# WinAI-OE — Accessibility Specification

Status: Normative for STAGE 02 accessibility work (PHASE 0147).

## 1. Control Contract

- Every `Button` in shipped XAML must carry `AutomationProperties.Name`.
- Emergency and approval controls must expose names even when restyled.
- `scripts/check_accessibility.py` enforces the contract without the .NET SDK.

## 2. Acceptance Criteria

- `tests/unit/test_phase_0147_a11y_spec.py` passes.
