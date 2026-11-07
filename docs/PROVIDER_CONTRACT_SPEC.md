# WinAI-OE — Provider Contract Specification

Status: Normative for STAGE 01 provider work (PHASE 0022).

## 1. Adapter Contract

Every adapter must implement `BaseModelProvider`:

- `complete(messages, tools, temperature, max_tokens)` → `ProviderResponse`.
- `stream(messages, tools, temperature, max_tokens)` → async token iterator.
- `get_capabilities()` → `ModelCapabilities` with honest flags for
  streaming, tools, vision, and context window.
- `health_check()` → bool; must never raise, never leak credentials.

## 2. Registry Rules

- Default provider is `mock`; unknown IDs raise `KeyError`.
- Official adapters instantiate only from vault-supplied keys.
- `list_providers()` exposes capabilities only, never secret material.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0022_provider_spec.py` passes.
- No adapter bypasses the registry to reach credentials directly.
