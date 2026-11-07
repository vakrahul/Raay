# WinAI-OE — Shutdown Specification

Status: Normative for STAGE 01 shutdown work (PHASE 0087).

## 1. Shutdown Contract

- `Ctrl+C` (`KeyboardInterrupt`) stops serving without traceback spam or
  orphaned sockets; the loopback port is releasable for immediate restart.
- Server binding always derives from validated `AppConfig`; no hard-coded
  host or port may appear in the entry script.
- `log_level` remains `"info"` so startup and shutdown lines stay visible.

## 2. Acceptance Criteria

- `tests/unit/test_phase_0087_shutdown_spec.py` passes.
