"""Unit tests for structured logging and audit integrity."""
import json
from pathlib import Path

import pytest

from src.security.audit_logger import AuditLogger, redact_secrets


@pytest.mark.unit
def test_secret_redaction():
    """Verify that sensitive tokens and API keys are redacted."""
    raw_text = "Sending request with key sk-proj-1234567890abcdef1234567890 to endpoint."
    redacted = redact_secrets(raw_text)
    assert "sk-proj-1234567890" not in redacted
    assert "[REDACTED_SECRET]" in redacted

    anthropic_text = "Key sk-ant-api03-abcdef1234567890abcdef123456"
    assert "sk-ant" not in redact_secrets(anthropic_text)

    gnani_text = "Header X-API-Key-ID: vach_0123456789abcdef0123456789abcdef_deadbeef sent"
    redacted_gnani = redact_secrets(gnani_text)
    assert "vach_0123456789" not in redacted_gnani
    assert "[REDACTED_SECRET]" in redacted_gnani

    sensitive_dict = {
        "user": "alice",
        "api_key": "supersecretkey123456",
        "headers": {
            "X-API-Key-ID": "raw_header_credential_value",
            "x-api-key": "raw_platform_credential_value",
            "Authorization": "CustomScheme secret_auth_value_999",
            "Proxy-Authorization": "Basic dXNlcjpwYXNz",
        },
        "nested": {
            "auth_token": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "normal_field": "hello",
        }
    }
    cleaned = redact_secrets(sensitive_dict)
    assert cleaned["user"] == "alice"
    assert cleaned["api_key"] == "[REDACTED_SECRET]"
    assert cleaned["headers"]["X-API-Key-ID"] == "[REDACTED_SECRET]"
    assert cleaned["headers"]["x-api-key"] == "[REDACTED_SECRET]"
    assert cleaned["headers"]["Authorization"] == "[REDACTED_SECRET]"
    assert cleaned["headers"]["Proxy-Authorization"] == "[REDACTED_SECRET]"
    assert cleaned["nested"]["normal_field"] == "hello"
    assert "Bearer eyJ" not in str(cleaned)

@pytest.mark.unit
def test_audit_logger_chain(temp_workspace: Path):
    """Verify audit logger writes chained entries and passes integrity check."""
    log_file = temp_workspace / "audit.jsonl"
    logger = AuditLogger(log_file)

    event1 = logger.log("AUTH_LOGIN", "User logged in", session_id="sess_1", operation_id="op_1")
    assert event1.prev_hash == "0" * 64
    assert len(event1.hash) == 64

    event2 = logger.log(
        "TOOL_INVOCATION", "Listing files", session_id="sess_1", operation_id="op_2"
    )
    assert event2.prev_hash == event1.hash

    assert logger.verify_integrity() is True

@pytest.mark.unit
def test_audit_logger_detects_tampering(temp_workspace: Path):
    """Verify that tampering with any record in the log file causes integrity check failure."""
    log_file = temp_workspace / "audit_tamper.jsonl"
    logger = AuditLogger(log_file)

    logger.log("EVENT_A", "Action A", session_id="s1")
    logger.log("EVENT_B", "Action B", session_id="s1")
    logger.log("EVENT_C", "Action C", session_id="s1")

    assert logger.verify_integrity() is True

    # Tamper with the second entry
    with open(log_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    record_to_tamper = json.loads(lines[1])
    record_to_tamper["message"] = "FORGED MESSAGE"
    lines[1] = json.dumps(record_to_tamper) + "\n"

    with open(log_file, "w", encoding="utf-8") as f:
        f.writelines(lines)

    # Integrity verification must now fail
    assert logger.verify_integrity() is False
