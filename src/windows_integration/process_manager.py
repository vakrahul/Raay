"""Windows Process Manager and Safeguard Engine (Phase 3).

Provides safe, policy-bounded process inspection and termination.
Protects critical Windows system processes (System, csrss, lsass, explorer, etc.)
from accidental or unauthorized termination. Enforces graceful closing (WM_CLOSE)
first to allow applications to save work cleanly.
"""

import ctypes
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import psutil
from pydantic import BaseModel
import win32con
import win32gui
import win32process

user32 = ctypes.windll.user32


class ProcessCloseResult(BaseModel):
    success: bool
    pid: int
    process_name: str
    method: str  # "WM_CLOSE_GRACEFUL", "TERMINATED", "KILLED", "NOT_FOUND", "PROTECTED"
    message: str


class WindowsProcessManager:
    """Manages process lifecycle with strict Windows system safeguards."""

    CRITICAL_SYSTEM_PROCESSES: Set[str] = {
        "system",
        "system idle process",
        "smss.exe",
        "csrss.exe",
        "wininit.exe",
        "services.exe",
        "lsass.exe",
        "lsm.exe",
        "winlogon.exe",
        "dwm.exe",
        "explorer.exe",
        "svchost.exe",
        "spoolsv.exe",
        "fontdrvhost.exe",
        "sihost.exe",
        "taskhostw.exe",
        "registry",
        "memory compression",
    }

    def __init__(self):
        pass

    def is_protected(self, pid: int) -> Tuple[bool, str]:
        """Check if target PID belongs to a critical Windows system process."""
        if pid <= 4:
            return True, f"PID {pid} is a core Windows kernel/system process and cannot be modified."

        try:
            p = psutil.Process(pid)
            name = p.name().lower()
            if name in self.CRITICAL_SYSTEM_PROCESSES:
                return True, f"Process '{name}' (PID {pid}) is a critical Windows OS service."
        except psutil.NoSuchProcess:
            return False, "Process does not exist."
        except psutil.AccessDenied:
            return True, f"PID {pid} requires elevated system privileges and is protected."

        return False, "Process is a user-level application."

    def find_pids_by_name(self, name_query: str) -> List[int]:
        """Locate running PIDs matching an executable or application name."""
        target = name_query.lower().strip()
        matched = []
        for p in psutil.process_iter(attrs=["pid", "name"]):
            try:
                p_name = (p.info.get("name") or "").lower()
                if target in p_name or target.replace(".exe", "") == p_name.replace(".exe", ""):
                    matched.append(p.info["pid"])
            except Exception:
                continue
        return matched

    def close_process(
        self,
        pid: int,
        force: bool = False,
        graceful_timeout: float = 3.0,
    ) -> ProcessCloseResult:
        """Close an application process gracefully via WM_CLOSE first, or force terminate if approved."""
        if not psutil.pid_exists(pid):
            return ProcessCloseResult(
                success=False,
                pid=pid,
                process_name="unknown",
                method="NOT_FOUND",
                message=f"Process with PID {pid} is not running.",
            )

        protected, reason = self.is_protected(pid)
        if protected:
            return ProcessCloseResult(
                success=False,
                pid=pid,
                process_name="protected_service",
                method="PROTECTED",
                message=f"Termination blocked: {reason}",
            )

        try:
            proc = psutil.Process(pid)
            p_name = proc.name()
        except Exception:
            p_name = "unknown"

        # 1. Attempt graceful close via WM_CLOSE to all top-level windows of this PID
        hwnds = []

        def enum_cb(hwnd, extra):
            try:
                _, win_pid = win32process.GetWindowThreadProcessId(hwnd)
                if win_pid == pid:
                    hwnds.append(hwnd)
            except Exception:
                pass
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(enum_cb), None)

        if hwnds:
            for h in hwnds:
                user32.PostMessageW(h, win32con.WM_CLOSE, 0, 0)

            # Wait for graceful termination
            start = time.time()
            while time.time() - start < graceful_timeout:
                if not psutil.pid_exists(pid):
                    return ProcessCloseResult(
                        success=True,
                        pid=pid,
                        process_name=p_name,
                        method="WM_CLOSE_GRACEFUL",
                        message=f"Application '{p_name}' (PID {pid}) closed gracefully via WM_CLOSE.",
                    )
                time.sleep(0.2)

        # 2. If force is enabled, terminate
        if force:
            try:
                proc = psutil.Process(pid)
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except psutil.TimeoutExpired:
                    proc.kill()

                return ProcessCloseResult(
                    success=True,
                    pid=pid,
                    process_name=p_name,
                    method="TERMINATED",
                    message=f"Process '{p_name}' (PID {pid}) terminated successfully.",
                )
            except Exception as e:
                return ProcessCloseResult(
                    success=False,
                    pid=pid,
                    process_name=p_name,
                    method="FAILED",
                    message=f"Failed to terminate PID {pid}: {e}",
                )

        return ProcessCloseResult(
            success=False,
            pid=pid,
            process_name=p_name,
            method="AWAITING_CONFIRMATION",
            message=f"Application '{p_name}' did not exit after WM_CLOSE. Forced termination requires user confirmation to prevent unsaved data loss.",
        )

    def close_application_by_name(
        self,
        app_name: str,
        force: bool = False,
    ) -> List[ProcessCloseResult]:
        """Find and close running instances of an application by name."""
        pids = self.find_pids_by_name(app_name)
        if not pids:
            return [
                ProcessCloseResult(
                    success=False,
                    pid=0,
                    process_name=app_name,
                    method="NOT_FOUND",
                    message=f"No running processes matching '{app_name}' found.",
                )
            ]

        results = []
        for pid in pids:
            results.append(self.close_process(pid=pid, force=force))
        return results

    def wait_for_process_start(self, pid: int, timeout: float = 5.0) -> bool:
        """Poll until process is running and initialized."""
        start = time.time()
        while time.time() - start < timeout:
            if psutil.pid_exists(pid):
                return True
            time.sleep(0.1)
        return False

    def wait_for_process_exit(self, pid: int, timeout: float = 5.0) -> bool:
        """Poll until process has completely exited."""
        start = time.time()
        while time.time() - start < timeout:
            if not psutil.pid_exists(pid):
                return True
            time.sleep(0.1)
        return False
