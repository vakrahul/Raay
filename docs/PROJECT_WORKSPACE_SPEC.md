# WinAI-OE — Project Workspace Specification

Status: Normative for STAGE 02 workspace work (PHASE 0127).

## 1. Descriptor Contract

- `root_path` must be absolute and canonical; relative roots are rejected.
- `security_level` is one of `strict`, `moderate`, `permissive`.
- `allowed_tools` must be a subset of the known tool vocabulary; unknown
  tools are rejected before dispatch.
- Existing project directories are never overwritten without explicit
  authorization.

## 2. Acceptance Criteria

- `tests/unit/test_phase_0127_workspace_spec.py` passes.
