"""PHASE 0179: renderer handles empty args and hostile keys safely."""
from src.orchestrator.task_dispatcher import render_tool_summary


def test_empty_arguments_renders_cleanly():
    assert render_tool_summary("noop", {}) == "noop()"


def test_case_insensitive_secret_matching():
    line = render_tool_summary("t", {"API_KEY": "x", "Password": "y", "safe": "z"})
    assert "x" not in line.split("API_KEY=")[1].split(",")[0]
    assert "safe=z" in line
