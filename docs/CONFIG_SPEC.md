# WinAI-OE — Configuration Specification

Status: Normative for STAGE 01 configuration work (PHASE 0017).

## 1. Sources and Precedence

1. Process environment (`WINAI_` prefix) — highest precedence.
2. `.env` file in the working directory.
3. Built-in safe defaults — lowest precedence.

## 2. Normative Defaults

- `host = 127.0.0.1` (loopback only; `0.0.0.0` and LAN addresses rejected
  with a security validation error).
- `port = 8765` within `1024–65535`.
- `security_level = strict`.
- `ipc_token`: fresh 32-byte hex per process unless explicitly provided.
- `workspace_root`, `data_dir`, `logs_dir`: absolute canonical paths.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0017_config_spec.py` passes.
- No secret material is accepted through, stored in, or echoed by config.
