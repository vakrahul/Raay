"""PHASE 0006: packaging manifests declare expected runtime and dev floors."""
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[2]


def test_pyproject_runtime_deps_present():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    text = "\n".join(deps).lower()
    for pkg in (
        "fastapi",
        "uvicorn",
        "pydantic",
        "websockets",
        "httpx",
        "sqlalchemy",
        "cryptography",
    ):
        assert pkg in text, f"missing runtime dependency floor: {pkg}"


def test_requirements_files_mirror_declared_floors():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").lower()
    assert "fastapi" in req and "pydantic" in req
    assert "-r requirements.txt" in dev
    assert "pytest" in dev and "ruff" in dev
    assert "password" not in req and "secret" not in req
