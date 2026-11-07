"""Enforce the normative top-level repository layout (PHASE 0003, hardened 0004).

Validates `docs/REPO_LAYOUT_SPEC.md` section 1 and 3 without modifying
runtime behavior. Exits 0 when compliant, 1 with violation list otherwise.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_ENTRIES = (
    "src",
    "tests",
    "docs",
    "scripts",
    "pyproject.toml",
    "requirements.txt",
    "pytest.ini",
    "run_vertical_slice.py",
    ".gitignore",
    "README.md",
)

REQUIRED_DOCS = (
    "REPO_LAYOUT_SPEC.md",
    "PRD.md",
    "ARCHITECTURE.md",
    "MASTER_ROADMAP_1000_PHASES.json",
    "ROADMAP_DEPENDENCIES.md",
    "PHASE_COMPLETION_POLICY.md",
    "IMPLEMENTATION_STATUS.md",
)

GITIGNORE_TOKENS = (
    "__pycache__/",
    "*.db",
    "*.log",
    "audit_logs/",
    ".winai/",
)


REQUIRED_DIRS = ("src", "tests", "docs", "scripts")
REQUIRED_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "pytest.ini",
    "run_vertical_slice.py",
    ".gitignore",
    "README.md",
)

FORBIDDEN_COMMITTED_NAMES = (".env", "id_rsa", "id_ed25519", "secrets.json")


def check_layout(root: Path = ROOT) -> list:
    violations = []
    for name in REQUIRED_ENTRIES:
        if not (root / name).exists():
            violations.append(f"missing required top-level entry: {name}")
    for name in REQUIRED_DIRS:
        p = root / name
        if p.exists() and not p.is_dir():
            violations.append(f"required directory is not a directory: {name}")
    for name in REQUIRED_FILES:
        p = root / name
        if p.exists() and not p.is_file():
            violations.append(f"required file is not a regular file: {name}")
    for name in REQUIRED_DOCS:
        if not (root / "docs" / name).exists():
            violations.append(f"missing required doc: {name}")
    gitignore_path = root / ".gitignore"
    if not gitignore_path.is_file():
        violations.append("missing required top-level entry: .gitignore")
        return violations
    gitignore = gitignore_path.read_text(encoding="utf-8")
    for token in GITIGNORE_TOKENS:
        if token not in gitignore:
            violations.append(f".gitignore must exclude {token}")
    for name in FORBIDDEN_COMMITTED_NAMES:
        if (root / name).exists():
            violations.append(f"secret-like file must not be committed at top level: {name}")
    return violations


def main() -> int:
    violations = check_layout()
    if violations:
        for v in violations:
            print(f"VIOLATION: {v}")
        return 1
    print("Repository layout compliant with docs/REPO_LAYOUT_SPEC.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
