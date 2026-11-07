# WinAI-OE — Python Packaging Specification

Status: Normative for STAGE 01 packaging work (PHASE 0007).

## 1. Manifest Roles

- `pyproject.toml [project].dependencies`: authoritative runtime floors.
- `requirements.txt`: deploy/runtime install list; must mirror pyproject floors.
- `requirements-dev.txt`: must start with `-r requirements.txt`, then add
  test/lint/type tooling only (pytest, pytest-asyncio, ruff, mypy).

## 2. Rules

- Runtime floors use `>=` lower bounds; exact pins or hashes are an L3
  implementation decision, not assumed here.
- No secret, credential, index URL with embedded auth, or local path may
  appear in any manifest.
- Dev-only packages must never leak into runtime manifests.
- Python floor: `requires-python = ">=3.11"`.

## 3. Acceptance Criteria

- All three manifests exist and parse.
- The 8 runtime families (fastapi, uvicorn, pydantic, pydantic-settings,
  websockets, httpx, sqlalchemy, cryptography) are present in both
  `pyproject.toml` and `requirements.txt`.
- `tests/unit/test_phase_0007_packaging_spec.py` passes.
