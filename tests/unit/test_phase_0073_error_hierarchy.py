"""PHASE 0073: typed error hierarchy with structured payloads."""
from src.orchestrator.errors import (
    ExecutionError,
    ProviderError,
    SecurityViolationError,
    ToolValidationError,
    WinAIError,
)


def test_hierarchy_and_payload_shape():
    err = SecurityViolationError("SEC_DENIED", "blocked", "request approval")
    assert isinstance(err, WinAIError)
    assert err.to_dict() == {
        "error_code": "SEC_DENIED",
        "message": "blocked",
        "remediation": "request approval",
    }
    for cls in (ToolValidationError, ProviderError, ExecutionError):
        assert issubclass(cls, WinAIError)
