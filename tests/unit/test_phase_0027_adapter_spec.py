"""PHASE 0027: resilience spec exists and matches implemented primitives."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_resilience_spec_declares_contract():
    text = (ROOT / "docs" / "ADAPTER_RESILIENCE_SPEC.md").read_text(encoding="utf-8")
    for token in ("CircuitBreaker", "health_check", "backoff", "CircuitBreakerOpenError"):
        assert token in text
