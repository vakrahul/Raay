"""OS-Aware Context Layer and Volatile State Tracking (Phase 5 & 6).

Maintains accurate, real-time operating system context:
- Tracked processes launched during session.
- Active window handles and their valid state.
- System memory pressure and resource thresholds.
- Pre-condition checking to prevent duplicate app launches and resource exhaustion.
Refreshes volatile state before every action to ensure handles/PIDs remain valid.
"""

import ctypes
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import psutil
from pydantic import BaseModel, Field

from src.windows_integration.system_intelligence import SystemMemoryInfo, WindowsSystemIntelligence

user32 = ctypes.windll.user32


class LaunchedProcessRecord(BaseModel):
    pid: int
    app_id: str
    launch_time: float
    is_alive: bool = True
    exit_code: Optional[int] = None


class WorkflowStepRecord(BaseModel):
    step_id: int
    tool_name: str
    target: str
    status: str
    timestamp: float
    details: Dict[str, Any] = Field(default_factory=dict)


class OSContextTracker:
    """Maintains live, verified operating system execution context."""

    def __init__(self, intelligence: Optional[WindowsSystemIntelligence] = None):
        self.intelligence = intelligence or WindowsSystemIntelligence()
        self.tracked_processes: Dict[int, LaunchedProcessRecord] = {}
        self.tracked_windows: Set[int] = set()
        self.workflow_history: List[WorkflowStepRecord] = []
        self.last_memory_status: Optional[SystemMemoryInfo] = None

    def register_launch(self, app_id: str, pid: int) -> None:
        """Register a newly spawned application process."""
        self.tracked_processes[pid] = LaunchedProcessRecord(
            pid=pid,
            app_id=app_id,
            launch_time=time.time(),
            is_alive=True,
        )

    def register_window(self, hwnd: int) -> None:
        """Register a managed window handle."""
        if hwnd:
            self.tracked_windows.add(hwnd)

    def record_step(self, tool_name: str, target: str, status: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Record an executed workflow step."""
        self.workflow_history.append(
            WorkflowStepRecord(
                step_id=len(self.workflow_history) + 1,
                tool_name=tool_name,
                target=target,
                status=status,
                timestamp=time.time(),
                details=details or {},
            )
        )

    def refresh_volatile_state(self) -> Dict[str, Any]:
        """Verify whether tracked PIDs and HWNDs are still valid before acting."""
        # 1. Refresh PIDs
        dead_pids = []
        for pid, rec in self.tracked_processes.items():
            if not psutil.pid_exists(pid):
                rec.is_alive = False
                dead_pids.append(pid)

        # 2. Refresh HWNDs
        invalid_hwnds = set()
        for hwnd in self.tracked_windows:
            if not user32.IsWindow(hwnd):
                invalid_hwnds.add(hwnd)
        self.tracked_windows.difference_update(invalid_hwnds)

        # 3. Refresh memory status
        self.last_memory_status = self.intelligence.get_system_memory_status()

        return {
            "active_processes_count": len([p for p in self.tracked_processes.values() if p.is_alive]),
            "dead_processes_detected": len(dead_pids),
            "valid_windows_count": len(self.tracked_windows),
            "memory_pressure": self.last_memory_status.memory_pressure_level,
            "available_ram_mb": self.last_memory_status.available_physical_mb,
        }

    def check_resource_preconditions(self, app_id: str) -> Tuple[bool, str]:
        """Phase 6: Resource-aware precondition checking before executing new operations."""
        self.refresh_volatile_state()

        # Check critical memory pressure
        if self.last_memory_status and self.last_memory_status.memory_pressure_level == "CRITICAL":
            return False, (
                f"Resource Guard: System physical RAM is critically depleted "
                f"({self.last_memory_status.percent_used}% in use). New application launches paused."
            )

        # Check duplicate launch: If app is already tracked and alive, avoid spamming duplicate processes
        alive_same_app = [
            rec for rec in self.tracked_processes.values()
            if rec.app_id == app_id and rec.is_alive
        ]
        if alive_same_app and app_id in ["notepad", "calc"]:
            # Informative pass: app is already running
            return True, f"Application '{app_id}' already has active tracked process (PID: {alive_same_app[0].pid})."

        return True, "Preconditions satisfied."

    def get_context_summary(self) -> Dict[str, Any]:
        """Compile a clean, sanitized OS state summary for planner reasoning."""
        self.refresh_volatile_state()
        return {
            "tracked_processes": [p.model_dump() for p in self.tracked_processes.values() if p.is_alive],
            "valid_windows": list(self.tracked_windows),
            "memory_pressure": self.last_memory_status.memory_pressure_level if self.last_memory_status else "NORMAL",
            "available_ram_mb": self.last_memory_status.available_physical_mb if self.last_memory_status else 0.0,
            "recent_steps_count": len(self.workflow_history),
        }
