"""Emergency Controls: Global kill switch, pause state, and process termination (Phase 59)."""
import asyncio
import os
import signal
from typing import Dict, List, Optional, Set
from pydantic import BaseModel


class EmergencyController:
    """Provides instant (<200ms) execution pause, cancellation, and process kill-switch."""

    def __init__(self):
        self._is_emergency_halted = False
        self._paused_sessions: Set[str] = set()
        self._active_pids: Set[int] = set()

    @property
    def is_halted(self) -> bool:
        return self._is_emergency_halted

    def register_pid(self, pid: int) -> None:
        self._active_pids.add(pid)

    def unregister_pid(self, pid: int) -> None:
        self._active_pids.discard(pid)

    def pause_session(self, session_id: str) -> None:
        self._paused_sessions.add(session_id)

    def resume_session(self, session_id: str) -> None:
        self._paused_sessions.discard(session_id)

    def is_session_paused(self, session_id: str) -> bool:
        return self._is_emergency_halted or (session_id in self._paused_sessions)

    def trigger_emergency_stop(self) -> Dict[str, int]:
        """Activate global emergency stop, terminating registered child processes."""
        self._is_emergency_halted = True
        killed_count = 0

        # Terminate active registered PIDs
        for pid in list(self._active_pids):
            try:
                if os.name == "nt":
                    # Windows taskkill forcefully terminating PID and child tree
                    os.system(f"taskkill /F /T /PID {pid} >nul 2>&1")
                else:
                    os.kill(pid, signal.SIGKILL)
                killed_count += 1
            except Exception:
                pass
            finally:
                self._active_pids.discard(pid)

        return {
            "status": "HALTED",
            "processes_killed": killed_count,
            "sessions_paused": len(self._paused_sessions),
        }

    def reset_emergency_stop(self) -> None:
        self._is_emergency_halted = False
        self._paused_sessions.clear()
