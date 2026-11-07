"""PHASE 0070: logging area sign-off with redaction plus chain round-trip."""
from src.security.audit_logger import AuditLogger


def test_logging_area_signoff(tmp_path):
    logger = AuditLogger(tmp_path / "audit.jsonl")
    event = logger.log(
        event_type="AUTH",
        message="login with sk-proj-secretvalue1234567890",
        payload={"api_key": "supersecret"},
    )
    assert "sk-proj-secretvalue" not in event.message
    assert event.payload["api_key"] == "[REDACTED_SECRET]"
    assert logger.event_count() == 1
    assert logger.verify_integrity() is True
