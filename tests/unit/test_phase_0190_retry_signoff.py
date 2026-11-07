"""PHASE 0190: retry area sign-off with transient recovery."""
import asyncio

from src.providers.resilience import retry_with_backoff


def test_retry_area_signoff():
    attempts = []

    async def _flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("transient")
        return "recovered"

    assert asyncio.run(retry_with_backoff(_flaky, max_retries=3, initial_delay=0.01)) == "recovered"
    assert len(attempts) == 3
