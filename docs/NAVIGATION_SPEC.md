# WinAI-OE — Navigation Specification

Status: Normative for STAGE 02 navigation work (PHASE 0107).

## 1. Destination Contract

Canonical destinations, in order: `Chat`, `Models`, `Agents`, `Security`,
`Memory`. Tags are stable identifiers consumed by view routing and
telemetry; renaming a tag is a breaking change requiring a migration note.

## 2. Rules

- Every destination tag in XAML must exist in `NAV_DESTINATIONS`.
- Unknown tags fail closed (no navigation, audit event recorded).
- The emergency control stays visible outside the navigation frame.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0107_nav_spec.py` passes.
