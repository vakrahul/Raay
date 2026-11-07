"""PHASE 0008: enforce packaging spec (docs/PACKAGING_SPEC.md)."""
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]

RUNTIME_FAMILIES = (
    "fastapi",
    "uvicorn",
    "pydantic",
    "pydantic-settings",
    "websockets",
    "httpx",
    "sqlalchemy",
    "cryptography",
)

DEV_ONLY = ("pytest", "pytest-asyncio", "ruff", "mypy")


def check_packaging(root: Path = ROOT) -> list:
    violations = []
    for manifest in ("pyproject.toml", "requirements.txt", "requirements-dev.txt"):
        if not (root / manifest).is_file():
            violations.append(f"missing required manifest: {manifest}")
    if violations:
        return violations
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except Exception as e:
        return [f"pyproject.toml unreadable: {e}"]
    deps = "\n".join(data.get("project", {}).get("dependencies", [])).lower()
    req = (root / "requirements.txt").read_text(encoding="utf-8").lower()
    dev = (root / "requirements-dev.txt").read_text(encoding="utf-8").lower()
    for fam in RUNTIME_FAMILIES:
        if fam not in deps:
            violations.append(f"pyproject.toml missing runtime family: {fam}")
        if fam not in req:
            violations.append(f"requirements.txt missing runtime family: {fam}")
    if "-r requirements.txt" not in dev:
        violations.append("requirements-dev.txt must include -r requirements.txt")
    for tool in DEV_ONLY:
        if tool in req:
            violations.append(f"dev-only package leaked into requirements.txt: {tool}")
    for label, text in (("requirements.txt", req), ("requirements-dev.txt", dev)):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        if len(lines) != len(set(lines)):
            violations.append(f"duplicate entries detected in {label}")
    for manifest in ("pyproject.toml", "requirements.txt", "requirements-dev.txt"):
        text = (root / manifest).read_text(encoding="utf-8").lower()
        for bad in ("password", "secret", "http://", "@"):
            if bad in text and "http" not in manifest:
                if bad == "http://":
                    violations.append(f"{manifest} must not embed insecure/auth URLs")
                elif bad in ("password", "secret"):
                    violations.append(f"{manifest} must not contain secrets: {bad}")
    return violations


def main() -> int:
    violations = check_packaging()
    if violations:
        for v in violations:
            print(f"VIOLATION: {v}")
        return 1
    print("Packaging compliant with docs/PACKAGING_SPEC.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
