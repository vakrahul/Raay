"""PHASE 0189: retry hardening — non-retryable errors surface immediately."""
import asyncio

from src.providers.resilience import is_retryable_status, retry_with_backoff


def test_non_retryable_exception_not_retried():
    calls = []

    async def _fails():
        calls.append(1)
        raise ValueError("client error")

    try:
        asyncio.run(retry_with_backoff(_fails, max_retries=3, retryable_exceptions=(KeyError,)))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert calls == [1]


def test_status_gate_types():
    assert is_retryable_status(429) is True
    assert is_retryable_status(0) is False
    assert is_retryable_status(599) is False
