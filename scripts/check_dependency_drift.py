"""PHASE 0093: detect drift between pyproject floors and requirements floors."""
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def normalize_family(spec: str) -> str:
    name = re.split(r"[<>=!~\s\[]", spec.strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def read_families(root: Path = ROOT) -> tuple:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    py_fams = {
        normalize_family(d)
        for d in data.get("project", {}).get("dependencies", [])
        if d.strip()
    }
    req_fams = set()
    for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if clean and not clean.startswith(("#", "-")):
            req_fams.add(normalize_family(clean))
    return py_fams, req_fams


def check_dependency_drift(root: Path = ROOT) -> list:
    try:
        py_fams, req_fams = read_families(root)
    except Exception as e:
        return [f"manifests unreadable: {e}"]
    violations = []
    for fam in sorted(py_fams - req_fams):
        violations.append(f"family in pyproject.toml missing from requirements.txt: {fam}")
    for fam in sorted(req_fams - py_fams):
        violations.append(f"family in requirements.txt missing from pyproject.toml: {fam}")
    return violations


def main() -> int:
    violations = check_dependency_drift()
    if violations:
        for v in violations:
            print(f"VIOLATION: {v}")
        return 1
    print("Dependencies in sync between pyproject.toml and requirements.txt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
