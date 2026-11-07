# WinAI-OE — Logging Specification

Status: Normative for STAGE 01 logging work (PHASE 0067).

## 1. Record Contract

- Every audit record carries ISO-8601 UTC `timestamp`, `event_type`,
  `level`, `message`, `payload`, `session_id`, `agent_id`,
  `operation_id`, `prev_hash`, and `hash`.
- `hash` is SHA-256 over canonical JSON (`sort_keys=True`, separators
  `(",", ":")`) of all fields except `hash` itself.

## 2. Redaction Rules

- `redact_secrets()` scrubs API keys, bearer tokens, passwords, and
  private-key material before persistence; secret-adjacent dict keys are
  replaced wholesale.

## 3. Integrity Rules

- `verify_integrity()` returns `True` only for an unbroken chain; any
  tampered, reordered, or corrupt line yields `False` (never raises).

## 4. Acceptance Criteria

- `tests/unit/test_phase_0067_logging_spec.py` passes.
