"""PHASE 0068: event counting reflects appended audit records."""
from src.security.audit_logger import AuditLogger


def test_event_count_tracks_appends(tmp_path):
    logger = AuditLogger(tmp_path / "audit.jsonl")
    assert logger.event_count() == 0
    logger.log(event_type="T", message="one")
    logger.log(event_type="T", message="two")
    assert logger.event_count() == 2
