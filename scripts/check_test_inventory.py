"""PHASE 0048: enforce test pyramid inventory (unit/integration/security)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SUITES = ("unit", "integration", "security")


def check_test_inventory(root: Path = ROOT) -> list:
    violations = []
    tests_dir = root / "tests"
    if not (tests_dir / "conftest.py").is_file():
        violations.append("missing tests/conftest.py")
    for suite in REQUIRED_SUITES:
        suite_dir = tests_dir / suite
        if not suite_dir.is_dir():
            violations.append(f"missing test suite directory: tests/{suite}")
            continue
        if not list(suite_dir.glob("test_*.py")):
            violations.append(f"empty test suite: tests/{suite}")
    return violations


def main() -> int:
    violations = check_test_inventory()
    if violations:
        for v in violations:
            print(f"VIOLATION: {v}")
        return 1
    print("Test inventory compliant with docs/TEST_HEALTH_SPEC.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
