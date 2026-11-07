"""Genuine Windows Control Service (Phase 2).

Unifies native Windows application launching, window management, accessibility control,
process management, and system intelligence into a cohesive, production-grade OS controller.
Zero in-memory mocks. Zero remote computer-vision fallbacks.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel

from src.windows_integration.app_manager import AppManager, ApprovedApp
from src.windows_integration.file_service import ScopedFileService
from src.windows_integration.process_manager import ProcessCloseResult, WindowsProcessManager
from src.windows_integration.process_runner import ProcessExecutionResult, RestrictedProcessRunner
from src.windows_integration.system_intelligence import (
    ProcessInfo,
    SystemMemoryInfo,
    SystemOverviewInfo,
    WindowInfo,
    WindowsSystemIntelligence,
)
from src.windows_integration.uia_service import UIAutomationService, UIElementInfo


class WindowsControlService:
    """Unified controller for genuine operating-system interaction on Microsoft Windows."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self.intelligence = WindowsSystemIntelligence()
        self.process_manager = WindowsProcessManager()
        self.app_manager = AppManager()
        self.uia = UIAutomationService()
        self.file_service = ScopedFileService(self.workspace_root)
        self.process_runner = RestrictedProcessRunner(self.workspace_root)

    # -------------------------------------------------------------------------
    # System Intelligence & Telemetry
    # -------------------------------------------------------------------------

    def get_system_overview(self) -> SystemOverviewInfo:
        return self.intelligence.get_system_overview()

    def get_memory_status(self) -> SystemMemoryInfo:
        return self.intelligence.get_system_memory_status()

    def list_processes(self, sort_by: str = "memory", limit: Optional[int] = 20) -> List[ProcessInfo]:
        return self.intelligence.list_processes(sort_by=sort_by, limit=limit)

    def get_top_resource_consumers(self, metric: str = "memory", limit: int = 10) -> List[ProcessInfo]:
        return self.intelligence.get_top_resource_consumers(metric=metric, limit=limit)

    def list_open_windows(self, visible_only: bool = True) -> List[WindowInfo]:
        return self.intelligence.list_top_level_windows(visible_only=visible_only)

    def get_foreground_window(self) -> Optional[WindowInfo]:
        return self.intelligence.get_foreground_window()

    # -------------------------------------------------------------------------
    # Application & Window Control
    # -------------------------------------------------------------------------

    def launch_application(self, app_id: str, extra_args: Optional[List[str]] = None) -> int:
        """Launch an approved application and return its process ID."""
        return self.app_manager.launch_app(app_id, extra_args=extra_args)

    def close_application(self, app_name_or_pid: Union[str, int], force: bool = False) -> ProcessCloseResult:
        """Close an application gracefully or forcefully."""
        if isinstance(app_name_or_pid, int):
            return self.process_manager.close_process(app_name_or_pid, force=force)
        results = self.process_manager.close_application_by_name(app_name_or_pid, force=force)
        return results[0] if results else ProcessCloseResult(
            success=False,
            pid=0,
            process_name=str(app_name_or_pid),
            method="NOT_FOUND",
            message=f"No running application matching '{app_name_or_pid}'",
        )

    def find_window(self, window_title: str, timeout_seconds: float = 5.0) -> Optional[Any]:
        return self.uia.find_window(window_title, timeout_seconds=timeout_seconds)

    def focus_window(self, window_title: str, timeout_seconds: float = 3.0) -> bool:
        return self.uia.focus_window(window_title, timeout_seconds=timeout_seconds)

    def wait_for_window(self, window_title: str, timeout_seconds: float = 5.0) -> bool:
        return self.uia.wait_for_window(window_title, timeout_seconds=timeout_seconds)

    def inspect_controls(self, window_title: str, max_depth: int = 3) -> List[UIElementInfo]:
        return self.uia.inspect_window_elements(window_title, max_depth=max_depth)

    def type_text(
        self,
        window_title: str,
        text: str,
        name: Optional[str] = None,
        control_type: Optional[str] = None,
        automation_id: Optional[str] = None,
        clear_first: bool = False,
    ) -> bool:
        return self.uia.set_text_value(
            window_title=window_title,
            text=text,
            name=name,
            control_type=control_type,
            automation_id=automation_id,
            clear_first=clear_first,
        )

    def send_keys(self, window_title: str, keys: str, wait_time: float = 0.05) -> bool:
        return self.uia.send_keys_to_window(window_title, keys, wait_time=wait_time)

    def click_control(
        self,
        window_title: str,
        name: Optional[str] = None,
        control_type: Optional[str] = None,
        automation_id: Optional[str] = None,
    ) -> bool:
        return self.uia.click_control(
            window_title=window_title,
            name=name,
            control_type=control_type,
            automation_id=automation_id,
        )

    def invoke_button(
        self,
        window_title: str,
        name: Optional[str] = None,
        automation_id: Optional[str] = None,
    ) -> bool:
        return self.uia.invoke_button(
            window_title=window_title,
            name=name,
            automation_id=automation_id,
        )

    def select_menu(self, window_title: str, menu_path: str) -> bool:
        return self.uia.select_menu_item(window_title, menu_path)

    # -------------------------------------------------------------------------
    # Filesystem & Subprocess Execution
    # -------------------------------------------------------------------------

    def read_file(self, rel_path: str) -> str:
        return self.file_service.read_file(rel_path)

    def write_file(self, rel_path: str, content: str) -> str:
        return self.file_service.write_file(rel_path, content)

    async def run_command(self, args: List[str]) -> ProcessExecutionResult:
        return await self.process_runner.run_command(args)
