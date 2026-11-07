"""PHASE 0066: logging primitives exist with redaction and chaining."""
import inspect

from src.security import audit_logger as audit_mod
from src.security.audit_logger import AuditEvent, AuditLogger


def test_logging_contracts_present():
    assert hasattr(audit_mod, "redact_secrets")
    assert hasattr(AuditLogger, "verify_integrity")
    src = inspect.getsource(AuditEvent)
    assert "prev_hash" in src and "sha256" in src.lower()
