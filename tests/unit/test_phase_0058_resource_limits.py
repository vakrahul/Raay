"""PHASE 0058: resource budget introspection without secret exposure."""
from src.orchestrator.config import get_config


def test_resource_limits_dict_reports_budgets():
    limits = get_config().resource_limits_dict()
    assert limits == {
        "subprocess_timeout_seconds": 60,
        "subprocess_max_memory_mb": 1024,
        "subprocess_max_output_bytes": 51200,
        "context_window_tokens": 8192,
    }
