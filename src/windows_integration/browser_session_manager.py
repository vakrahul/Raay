"""Shared Chrome and Browser Session Manager with Task Locking and Concurrency Control (Phase 6)."""
import asyncio
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.windows_integration.browser_service import BrowserActionResult, BrowserService


class BrowserSessionLease(BaseModel):
    owner_agent_id: str
    task_id: str
    acquired_at: float = Field(default_factory=time.time)
    lease_duration_seconds: float = 60.0
    active_url: str = ""
    active_tab_title: str = ""

    @property
    def is_expired(self) -> bool:
        return time.time() - self.acquired_at > self.lease_duration_seconds


class SharedBrowserSessionManager:
    """Manages multi-agent access to Google Chrome, preventing race conditions and tab conflicts."""

    def __init__(self, browser_service: Optional[BrowserService] = None):
        self.browser_service = browser_service or BrowserService()
        self._lock = asyncio.Lock()
        self._active_lease: Optional[BrowserSessionLease] = None
        self._navigation_history: List[Dict[str, Any]] = []
        self._download_records: List[str] = []

    async def acquire_session(
        self,
        agent_id: str,
        task_id: str,
        timeout_seconds: float = 10.0,
    ) -> bool:
        """Acquire exclusive lease on the browser session for an agent."""
        start = time.time()
        while time.time() - start < timeout_seconds:
            async with self._lock:
                if self._active_lease is None or self._active_lease.is_expired:
                    self._active_lease = BrowserSessionLease(
                        owner_agent_id=agent_id,
                        task_id=task_id,
                    )
                    return True
            await asyncio.sleep(0.2)
        return False

    async def release_session(self, agent_id: str) -> bool:
        """Release the active browser lease."""
        async with self._lock:
            if self._active_lease and self._active_lease.owner_agent_id == agent_id:
                self._active_lease = None
                return True
        return False

    def get_session_status(self) -> Dict[str, Any]:
        return {
            "is_busy": self._active_lease is not None and not self._active_lease.is_expired,
            "current_owner": self._active_lease.owner_agent_id if self._active_lease else None,
            "active_url": self._active_lease.active_url if self._active_lease else None,
            "active_title": self._active_lease.active_tab_title if self._active_lease else None,
            "history_count": len(self._navigation_history),
        }

    async def execute_browser_task(
        self,
        agent_id: str,
        task_id: str,
        action_coroutine_func: Any,
    ) -> Any:
        """Executes a browser task safely within an acquired session lock."""
        acquired = await self.acquire_session(agent_id, task_id)
        if not acquired:
            raise TimeoutError(
                f"Browser Session Busy: Agent '{agent_id}' could not acquire browser lock within timeout. "
                f"Currently locked by: '{self._active_lease.owner_agent_id if self._active_lease else 'unknown'}'"
            )

        try:
            result = await action_coroutine_func(self.browser_service)

            # Record history
            if isinstance(result, BrowserActionResult):
                if self._active_lease:
                    self._active_lease.active_url = result.url
                    self._active_lease.active_tab_title = result.page_title
                self._navigation_history.append({
                    "url": result.url,
                    "title": result.page_title,
                    "agent_id": agent_id,
                    "timestamp": time.time(),
                })
            return result

        finally:
            await self.release_session(agent_id)
