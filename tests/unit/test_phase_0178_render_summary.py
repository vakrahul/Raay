"""PHASE 0178: redacted one-line tool summaries with truncation."""
from src.orchestrator.task_dispatcher import render_tool_summary


def test_render_summary_redacts_and_truncates():
    line = render_tool_summary("fs_write_file", {"path": "a.txt", "api_key": "supersecret"})
    assert line.startswith("fs_write_file(")
    assert "supersecret" not in line
    assert "[REDACTED]" in line
    long_line = render_tool_summary("t", {"big": "x" * 500}, max_chars=200)
    assert len(long_line) <= 200
