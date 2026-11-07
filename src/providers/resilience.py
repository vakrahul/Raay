"""Provider reliability: exponential backoff, rate limiting, and circuit breaker."""
import asyncio
import random
import time
from typing import Any, Callable, Coroutine, Optional
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "CLOSED"       # Normal operation
    OPEN = "OPEN"           # Failing, requests blocked
    HALF_OPEN = "HALF_OPEN" # Probing service recovery


class CircuitBreakerOpenError(Exception):
    """Raised when request is attempted while circuit breaker is open."""
    pass


class CircuitBreaker:
    """Protects providers from cascading failures and excessive downstream errors."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_state_change = time.time()

    def record_success(self):
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self):
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.last_state_change = time.time()

    def allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_state_change > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        if self.state == CircuitState.HALF_OPEN:
            return True
        return False


RETRYABLE_HTTP_STATUSES = (408, 429, 500, 502, 503, 504)


def is_retryable_status(status_code: int) -> bool:
    """Return True for transient HTTP statuses worth retrying."""
    return status_code in RETRYABLE_HTTP_STATUSES


async def retry_with_backoff(
    func: Callable[[], Coroutine[Any, Any, Any]],
    max_retries: int = 3,
    initial_delay: float = 0.5,
    backoff_factor: float = 2.0,
    retryable_exceptions: tuple = (Exception,),
) -> Any:
    """Execute async coroutine with jittered exponential backoff."""
    delay = initial_delay
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return await func()
        except retryable_exceptions as e:
            last_exception = e
            if attempt == max_retries:
                raise e
            # Add jitter to avoid thundering herd
            jittered_delay = delay * (0.8 + 0.4 * random.random())
            await asyncio.sleep(jittered_delay)
            delay *= backoff_factor

    raise last_exception or RuntimeError("Retry loop exhausted")
