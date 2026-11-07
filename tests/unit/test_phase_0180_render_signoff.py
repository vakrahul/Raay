"""PHASE 0180: rendering area sign-off via dispatcher fallback path."""
import asyncio
from pathlib import Path

from src.orchestrator.task_dispatcher import AutonomousTaskDispatcher


def test_render_area_signoff():
    dispatcher = AutonomousTaskDispatcher(workspace_root=Path.cwd())
    result = asyncio.run(dispatcher.execute_task("summarize the workspace status quo"))
    assert result.status == "COMPLETED"
    assert result.summary
