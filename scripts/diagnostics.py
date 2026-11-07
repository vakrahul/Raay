"""PHASE 0098: operator diagnostics report (secret-free, offline-first)."""
import importlib.metadata as metadata
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def collect_diagnostics() -> dict:
    from src.orchestrator.config import AppConfig
    from src.orchestrator.main import create_app, list_registered_routes
    from scripts.check_dependency_drift import check_dependency_drift
    from scripts.check_packaging import check_packaging
    from scripts.check_repo_layout import check_layout
    from scripts.check_test_inventory import check_test_inventory

    packages = {}
    for dist in ("fastapi", "pydantic", "httpx", "uvicorn"):
        try:
            packages[dist] = metadata.version(dist)
        except Exception:
            packages[dist] = "unknown"

    app = create_app(AppConfig(environment="testing"))
    return {
        "python": platform.python_version(),
        "packages": packages,
        "routes": [path for path, _ in list_registered_routes(app)],
        "violations": {
            "layout": check_layout(ROOT),
            "packaging": check_packaging(ROOT),
            "drift": check_dependency_drift(ROOT),
            "test_inventory": check_test_inventory(ROOT),
        },
    }


def main() -> int:
    data = collect_diagnostics()
    print(f"Python: {data['python']}")
    for name, ver in data["packages"].items():
        print(f"{name}: {ver}")
    display_routes = [
        r for r in data["routes"] if not any(tok in r.lower() for tok in ("password", "secret"))
    ]
    print(f"Routes ({len(data['routes'])}): {', '.join(display_routes)}")
    failed = False
    for check, violations in data["violations"].items():
        if violations:
            failed = True
            for v in violations:
                print(f"VIOLATION [{check}]: {v}")
    if not failed:
        print("All diagnostics checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
