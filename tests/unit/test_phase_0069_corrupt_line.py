"""PHASE 0069: corrupt log lines fail verification closed instead of raising."""
from src.security.audit_logger import AuditLogger


def test_corrupt_line_returns_false(tmp_path):
    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_file)
    logger.log(event_type="T", message="good")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write("NOT VALID JSON{{{\n")
    assert logger.verify_integrity() is False
