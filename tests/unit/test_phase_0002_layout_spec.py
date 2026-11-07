"""PHASE 0002: top-level layout spec acceptance checks (docs-only)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_normative_top_level_entries_exist():
    for name in (
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
    ):
        assert (ROOT / name).exists(), f"missing required top-level entry: {name}"


def test_docs_index_and_spec_present():
    docs = ROOT / "docs"
    for name in (
        "REPO_LAYOUT_SPEC.md",
        "PRD.md",
        "ARCHITECTURE.md",
        "MASTER_ROADMAP_1000_PHASES.json",
        "ROADMAP_DEPENDENCIES.md",
        "PHASE_COMPLETION_POLICY.md",
        "IMPLEMENTATION_STATUS.md",
    ):
        assert (docs / name).exists(), f"missing required doc: {name}"


def test_gitignore_excludes_runtime_artifacts():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for token in ("__pycache__/", "*.db", "*.log", "audit_logs/", ".winai/"):
        assert token in text, f".gitignore must exclude {token}"
