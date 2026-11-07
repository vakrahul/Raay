"""PHASE 0186: bounded retry primitive exists with explicit configuration."""
import inspect

from src.providers import resilience as resilience_mod
from src.providers.resilience import retry_with_backoff


def test_retry_primitive_bounded():
    sig = inspect.signature(retry_with_backoff)
    assert sig.parameters["max_retries"].default == 3
    assert "retryable_exceptions" in sig.parameters
    assert hasattr(resilience_mod, "CircuitBreaker")
