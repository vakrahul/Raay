"""PHASE 0059: out-of-range budgets fail closed at construction."""
import pytest
from pydantic import ValidationError

from src.orchestrator.config import AppConfig


def test_budget_bounds_reject_extremes():
    with pytest.raises(ValidationError):
        AppConfig(subprocess_timeout_seconds=0)
    with pytest.raises(ValidationError):
        AppConfig(subprocess_max_memory_mb=64)
    with pytest.raises(ValidationError):
        AppConfig(subprocess_max_output_bytes=10**9)
    with pytest.raises(ValidationError):
        AppConfig(context_window_tokens=512)
