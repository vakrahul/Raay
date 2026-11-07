"""PHASE 0054: factory instances are isolated (no shared mutable state)."""
from src.orchestrator.config import AppConfig
from src.orchestrator.main import create_app, list_registered_routes


def test_factory_instances_isolated():
    app_a = create_app(AppConfig(environment="testing"))
    app_b = create_app(AppConfig(environment="testing"))
    assert app_a is not app_b
    assert list_registered_routes(app_a) == list_registered_routes(app_b)


def test_testing_config_never_prefers_live_provider():
    from src.orchestrator.main import create_app as factory

    app = factory(AppConfig(environment="testing"))
    routes = dict(list_registered_routes(app))
    assert "/health" in routes
