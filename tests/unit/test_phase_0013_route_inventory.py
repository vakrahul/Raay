"""PHASE 0013: route inventory helper reflects the entry-point contract."""
from src.orchestrator.main import create_app, list_registered_routes
from src.orchestrator.config import AppConfig


def test_route_inventory_contains_core_surface():
    app = create_app(AppConfig(environment="testing"))
    routes = dict(list_registered_routes(app))
    for path in ("/health", "/dashboard", "/api/v1/chat", "/api/v1/config"):
        assert path in routes, f"missing route in inventory: {path}"
    assert "GET" in routes["/health"]
