# WinAI-OE — Application Lifecycle Specification

Status: Normative for STAGE 01 lifecycle work (PHASE 0052).

## 1. Startup Order

1. Load and validate `AppConfig` (fail fast on invalid host/port).
2. Construct `SecurityPolicyEngine` before any provider or tool wiring.
3. Initialize `CredentialVault` and `ProviderRegistry` from vault keys.
4. Construct scanners, managers, and the task dispatcher.
5. Register routes; expose `list_registered_routes()` for verification.

## 2. Rules

- No route may dispatch privileged work without policy evaluation.
- Module-level `app = create_app()` must remain import-safe (no network
  I/O at import time beyond local vault reads).

## 3. Acceptance Criteria

- `tests/unit/test_phase_0052_lifecycle_spec.py` passes.
