# WinAI-OE — WinUI Shell Specification

Status: Normative for STAGE 02 shell work (PHASE 0102).

## 1. Project Contract

- Target `net8.0-windows10.0.19041.0`, `UseWinUI=true`, nullable enabled.
- Package references stay pinned (WindowsAppSDK, BuildTools, MVVM toolkit).
- Manifest keeps Windows 10/11 compatibility plus PerMonitorV2 awareness.

## 2. Verification Without the .NET SDK

- `scripts/check_client_shell.py` parses every `.csproj`, `.manifest`,
  and `.xaml` file as XML; any malformed file is a violation.
- Python-side contract tests mirror C# model field names.

## 3. Acceptance Criteria

- `tests/unit/test_phase_0102_shell_spec.py` passes.
