"""Unit tests for SystemAppScanner and application discovery."""
import pytest
from starlette.testclient import TestClient

from src.orchestrator.main import create_app
from src.windows_integration.system_app_scanner import SystemAppScanner


@pytest.mark.unit
def test_system_app_scanner():
    """Verify system scanner detects installed apps and computes summary metrics."""
    scanner = SystemAppScanner()
    apps = scanner.scan_all_applications()
    assert len(apps) > 10, f"Expected at least 10 apps, found {len(apps)}"

    stats = scanner.get_summary_stats()
    assert stats["total_applications"] == len(apps)
    assert "Developer Tool" in stats["category_breakdown"]
    assert "Web Browser" in stats["category_breakdown"]

    # Verify Chrome is identified
    chrome_app = next((a for a in apps if "chrome" in a.app_id.lower() or "chrome" in a.display_name.lower()), None)
    assert chrome_app is not None
    assert chrome_app.category.value == "Web Browser"


@pytest.mark.integration
def test_system_apps_endpoint():
    """Verify GET /api/v1/system/apps returns apps and stats."""
    app = create_app()
    with TestClient(app) as client:
        res = client.get("/api/v1/system/apps")
        assert res.status_code == 200
        data = res.json()
        assert "stats" in data
        assert "applications" in data
        assert data["stats"]["total_applications"] > 10

        # Verify dashboard HTML endpoint
        dash_res = client.get("/dashboard")
        assert dash_res.status_code == 200
        assert "Enterprise Autonomous AI Employee" in dash_res.text
        assert "Operating Environment" in dash_res.text
