"""PHASE 0188: retryable-status table separates transient from client errors."""
from src.providers.resilience import RETRYABLE_HTTP_STATUSES, is_retryable_status


def test_retryable_status_table():
    assert set(RETRYABLE_HTTP_STATUSES) == {408, 429, 500, 502, 503, 504}
    for code in (408, 429, 500, 502, 503, 504):
        assert is_retryable_status(code) is True
    for code in (200, 400, 401, 403, 404):
        assert is_retryable_status(code) is False
