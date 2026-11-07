"""Controlled Windows Application Discovery and Launching (Phases 61-63)."""
import os
import subprocess
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ApprovedApp(BaseModel):
    app_id: str
    display_name: str
    binary_path: str
    allowed_arguments: List[str] = Field(default_factory=list)
    requires_approval: bool = True


class AppManager:
    """Discovers and controls launching of approved Windows applications."""

    DEFAULT_APPROVED = [
        ApprovedApp(
            app_id="notepad",
            display_name="Notepad",
            binary_path=os.path.expandvars(r"%SystemRoot%\System32\notepad.exe"),
            requires_approval=False,
        ),
        ApprovedApp(
            app_id="calc",
            display_name="Calculator",
            binary_path=os.path.expandvars(r"%SystemRoot%\System32\calc.exe"),
            requires_approval=False,
        ),
        ApprovedApp(
            app_id="mspaint",
            display_name="Microsoft Paint",
            binary_path=os.path.expandvars(r"%LocalAppData%\Microsoft\WindowsApps\mspaint.exe"),
            requires_approval=False,
        ),
        ApprovedApp(
            app_id="chrome",
            display_name="Google Chrome",
            binary_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            requires_approval=False,
        ),
        ApprovedApp(
            app_id="vscode",
            display_name="Visual Studio Code",
            binary_path=os.path.expandvars(r"%LocalAppData%\Programs\Microsoft VS Code\Code.exe"),
            requires_approval=False,
        ),
        ApprovedApp(
            app_id="edge",
            display_name="Microsoft Edge",
            binary_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            requires_approval=False,
        ),
    ]

    def __init__(self):
        self._approved_apps: Dict[str, ApprovedApp] = {
            app.app_id: app for app in self.DEFAULT_APPROVED
        }
        self._active_processes: Dict[str, int] = {}  # app_id -> pid

    def list_approved_apps(self) -> List[ApprovedApp]:
        return list(self._approved_apps.values())

    def register_app(self, app: ApprovedApp) -> None:
        self._approved_apps[app.app_id] = app

    def can_launch(self, app_id: str) -> bool:
        return app_id in self._approved_apps

    def launch_app(self, app_id: str, extra_args: Optional[List[str]] = None) -> int:
        """Launch an approved application in a controlled, non-elevated process."""
        if not self.can_launch(app_id):
            raise PermissionError(f"Application '{app_id}' is not in the approved whitelist.")

        app = self._approved_apps[app_id]
        cmd = [app.binary_path]
        if extra_args:
            cmd.extend(extra_args)

        proc = subprocess.Popen(  # noqa: S603
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        self._active_processes[app_id] = proc.pid
        return proc.pid

    def get_running_pid(self, app_id: str) -> Optional[int]:
        return self._active_processes.get(app_id)
