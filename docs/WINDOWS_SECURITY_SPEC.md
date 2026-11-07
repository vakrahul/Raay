# WinAI-OE — Windows Integration and Security Specification

Status: Normative for STAGE 01 integration work (PHASE 0042).

## 1. Filesystem Rules

- All paths resolve via canonical `realpath` and must remain inside the
  workspace root; traversal, ADS streams, and system directories are denied.
- Every overwrite creates an atomic backup first; rollback restores bytes.

## 2. Execution Rules

- Subprocesses spawn with `shell=False` argument arrays, scrubbed
  environments, enforced timeouts, and truncated output buffers.
- Browser sessions are lease-locked per agent; cookies and tokens never
  enter model prompts.

## 3. Policy Rules

- Every tool call evaluates to ALLOW, DENY, or REQUIRE_APPROVAL outside
  model prompt space; unknown tools fail closed.
- Approvals are single-use CSPRNG nonces bound to action hashes with TTL.

## 4. Acceptance Criteria

- `tests/unit/test_phase_0042_windows_security_spec.py` passes.
- No new execution path bypasses the policy engine.
