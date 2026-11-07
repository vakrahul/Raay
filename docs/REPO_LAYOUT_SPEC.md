# WinAI-OE — Top-Level Repository Layout Specification

Status: Normative for STAGE 01. Informative diagrams in `README.md` and
`docs/REPO_STRUCTURE.md` must not contradict this file.

## 1. Normative Top-Level Entries

Required at repository root:

- `src/` — product source only (`orchestrator/`, `providers/`, `security/`,
  `storage/`, `windows_integration/`, `client/WinAI.Client/`).
- `tests/` — `unit/`, `integration/`, `security/`, plus `conftest.py`.
- `docs/` — architecture, security, roadmap, status, and per-phase reports.
- `scripts/` — deterministic generators and maintenance tooling.
- `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `pytest.ini`.
- `run_vertical_slice.py`, `chat_cli.py`.
- `.gitignore`, `.editorconfig`, `README.md`.

No runtime secrets, databases (`*.db`, `*.sqlite3`), logs, or `audit_logs/`
may be committed. Local experiment artifacts (screenshots, captures, scratch
`.py` files) must remain untracked and must never be required for tests.

## 2. Documentation Index

Canonical reading order:

1. `README.md`
2. `docs/PRD.md`
3. `docs/ARCHITECTURE.md`
4. `docs/REPO_STRUCTURE.md` plus this file for normative rules
5. `docs/THREAT_MODEL.md`, `docs/TRUST_BOUNDARIES.md`
6. `docs/AUTONOMOUS_AGENT_ARCHITECTURE_AUDIT.md`
7. `docs/MASTER_ROADMAP_1000_PHASES.md` (.json is machine-readable source)
8. `docs/ROADMAP_DEPENDENCIES.md`, `docs/PHASE_COMPLETION_POLICY.md`,
   `docs/IMPLEMENTATION_STATUS.md`
9. `docs/DEVELOPMENT_SETUP.md`, `docs/CODING_STANDARDS.md`
10. Per-phase reports: `docs/PHASE_*.md`

## 3. Acceptance Criteria

- All section-1 entries exist in a clean checkout.
- `docs/` contains every section-2 document.
- `.gitignore` excludes `__pycache__/`, `*.db`, `*.sqlite3`, `*.log`,
  `logs/`, `audit_logs/`, `.winai/`, `.vs/`, `bin/`, `obj/`.
- `tests/unit/test_phase_0002_layout_spec.py` passes.
- No change to runtime behavior was required by this phase.
