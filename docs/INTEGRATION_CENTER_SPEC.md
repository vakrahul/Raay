# WinAI-OE — Integration Center Specification

Status: Normative for STAGE 02 integration work (PHASE 0132).

## 1. Connection Contract

- Each connection shows provider name, selected model, endpoint host,
  `IsLocal` flag, credential-stored flag, and live health state.
- Local providers (`IsLocal=true`, loopback endpoints) never display cloud
  egress warnings; cloud providers always disclose data-egress status.

## 2. Rules

- `HasCredentialStored` reflects vault state only, never key material.
- `describe_connection()` reports metadata synchronously; health probing
  stays asynchronous and non-blocking.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0132_integration_spec.py` passes.
