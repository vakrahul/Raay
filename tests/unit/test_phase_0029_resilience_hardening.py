"""PHASE 0029: breaker transitions and registration edge cases."""
import pytest

from src.providers.mock_provider import MockProvider
from src.providers.registry import ProviderRegistry
from src.providers.resilience import CircuitBreaker, CircuitBreakerOpenError, CircuitState


def test_breaker_opens_after_threshold_and_recovers():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=3600.0)
    assert cb.state == CircuitState.CLOSED
    cb.record_failure()
    assert cb.allow_request() is True
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_duplicate_registration_replaces_without_duplicating_ids():
    reg = ProviderRegistry()
    reg.register_provider("dup", MockProvider())
    reg.register_provider("dup", MockProvider())
    assert reg.registered_ids().count("dup") == 1
    with pytest.raises(TypeError):
        reg.register_provider("bad", "not-a-provider")
