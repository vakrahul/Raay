"""Stage 7: minimized context, redaction, and memory separation."""
from src.orchestrator.brain.brain_subsystem import BrainSubsystem
from src.orchestrator.jev.decision_context import (
    JevDecisionLog,
    filter_allowed_fields,
    pack_decision_context,
)


def test_pack_truncates_long_descriptions():
    pack = pack_decision_context("x" * 2000, ["a", "b"], max_chars=100)
    assert len(pack.task_description) <= 101
    assert pack.provenance["truncated"] is True
    assert pack.provenance["source"] == "jev_context_pack"


def test_pack_redacts_secret_patterns():
    pack = pack_decision_context(
        "Deploy with key sk-proj-abcdef1234567890abcdef please.",
        ["deploy", "skip"],
    )
    assert "sk-proj-abcdef" not in pack.task_description
    assert "[REDACTED" in pack.task_description


def test_filter_drops_sensitive_and_unlisted_keys():
    raw = {
        "task_description": "Do the thing.",
        "task_type": "general",
        "candidates": ["a", "b"],
        "api_key": "sk-secret",
        "password": "hunter2",
        "session_cookie": "abc",
        "project_directory": "/home/user/code",
        "entire_file_contents": "x" * 5000,
    }
    filtered = filter_allowed_fields(raw)
    assert set(filtered) == {"task_description", "task_type", "candidates"}


def test_extra_fields_cannot_override_or_smuggle():
    pack = pack_decision_context(
        "Real task.",
        ["a"],
        extra={
            "task_description": "INJECTED OVERRIDE",
            "api_key": "sk-evil",
            "task_type": "general",
        },
    )
    assert pack.task_description == "Real task."
    assert "sk-evil" not in str(pack.model_dump())


def test_decision_log_bounded_and_ephemeral(tmp_path):
    log = JevDecisionLog(max_entries=5)
    for i in range(8):
        log.record(f"req-{i}", "task_classification", "simple", 0.7, "mock-jev")
    assert len(log) == 5
    assert [e.request_id for e in log.recent(2)] == ["req-6", "req-7"]
    assert log.recent(0) == []
    log.clear()
    assert len(log) == 0


def test_decision_history_never_touches_user_memory(tmp_path):
    brain = BrainSubsystem(workspace_root=tmp_path, db_path=tmp_path / "m7.db")
    log = JevDecisionLog()
    pack = pack_decision_context("User prefers dark dashboards.", ["a", "b"])
    log.record(pack.request_id, "task_classification", "a", 0.6, "mock-jev")
    assert brain.memory_stats()["total"] == 0
    assert brain.inspect_memory() == []
