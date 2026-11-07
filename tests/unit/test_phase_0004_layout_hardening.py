"""PHASE 0004: layout checker edge cases and secret hygiene."""
from scripts.check_repo_layout import check_layout


def test_missing_entry_detected(tmp_path):
    (tmp_path / ".gitignore").write_text("__pycache__/\n*.db\n*.log\naudit_logs/\n.winai/\n")
    violations = check_layout(tmp_path)
    assert any("missing required top-level entry: src" in v for v in violations)


def test_secret_like_top_level_file_flagged(tmp_path):
    for name in ("src", "tests", "docs", "scripts"):
        (tmp_path / name).mkdir()
    for name in (
        "pyproject.toml",
        "requirements.txt",
        "pytest.ini",
        "run_vertical_slice.py",
        ".gitignore",
        "README.md",
    ):
        (tmp_path / name).write_text("x")
    (tmp_path / ".env").write_text("SECRET=1")
    violations = check_layout(tmp_path)
    assert any("secret-like file" in v for v in violations)


def test_live_tree_still_compliant():
    assert check_layout() == []
