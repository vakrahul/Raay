"""Security anomaly monitoring and automated remediation recovery (Phases 79-80)."""
import time
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class SecurityAlert(BaseModel):
    alert_id: str
    session_id: str
    agent_id: str
    alert_type: str
    severity: str
    description: str
    timestamp: float = Field(default_factory=time.time)


class AnomalyMonitor:
    """Detects repeated authorization failures, rapid tool bursts, and suspicious patterns."""

    def __init__(self, failure_threshold: int = 3, burst_window_seconds: float = 2.0, max_burst: int = 10):
        self.failure_threshold = failure_threshold
        self.burst_window = burst_window_seconds
        self.max_burst = max_burst

        # agent_id -> consecutive failure count
        self._consecutive_failures: Dict[str, int] = {}
        # agent_id -> list of recent timestamps
        self._tool_call_timestamps: Dict[str, List[float]] = {}
        # Quarantined agents
        self._quarantined_agents: set = set()
        self.alerts: List[SecurityAlert] = []

    def record_authorization_failure(self, agent_id: str, session_id: str, reason: str) -> Optional[SecurityAlert]:
        """Track failure and quarantine agent if threshold reached."""
        count = self._consecutive_failures.get(agent_id, 0) + 1
        self._consecutive_failures[agent_id] = count

        if count >= self.failure_threshold:
            self._quarantined_agents.add(agent_id)
            alert = SecurityAlert(
                alert_id=f"alt_sec_{int(time.time() * 1000)}",
                session_id=session_id,
                agent_id=agent_id,
                alert_type="REPEATED_AUTH_FAILURES",
                severity="CRITICAL",
                description=f"Agent '{agent_id}' exceeded failure threshold ({count}). Automatically quarantined.",
            )
            self.alerts.append(alert)
            return alert
        return None

    def record_authorization_success(self, agent_id: str) -> None:
        self._consecutive_failures[agent_id] = 0

    def record_tool_call(self, agent_id: str, session_id: str) -> Optional[SecurityAlert]:
        """Detect rapid tool call bursts."""
        now = time.time()
        calls = self._tool_call_timestamps.setdefault(agent_id, [])
        calls.append(now)

        # Filter out old calls outside window
        calls = [t for t in calls if now - t <= self.burst_window]
        self._tool_call_timestamps[agent_id] = calls

        if len(calls) > self.max_burst:
            self._quarantined_agents.add(agent_id)
            alert = SecurityAlert(
                alert_id=f"alt_burst_{int(time.time() * 1000)}",
                session_id=session_id,
                agent_id=agent_id,
                alert_type="ABNORMAL_TOOL_BURST",
                severity="HIGH",
                description=f"Agent '{agent_id}' triggered {len(calls)} tool calls within {self.burst_window}s.",
            )
            self.alerts.append(alert)
            return alert
        return None

    def is_quarantined(self, agent_id: str) -> bool:
        return agent_id in self._quarantined_agents

    def lift_quarantine(self, agent_id: str) -> None:
        self._quarantined_agents.discard(agent_id)
        self._consecutive_failures[agent_id] = 0
