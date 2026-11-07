"""Unit tests for Stage VIII: Advanced Execution Isolation and Anomaly Monitoring."""
from pathlib import Path
import pytest

from src.security.anomaly_monitor import AnomalyMonitor
from src.security.isolation_sandbox import IsolationSandbox, NetworkPolicy, ResourceQuota
from src.security.privilege_guard import PrivilegeGuard


@pytest.mark.security
def test_sandbox_network_policy(temp_workspace: Path):
    """Verify network policy enforcement (blocking egress and enforcing domain whitelists)."""
    # 1. Default disabled network
    sandbox_disabled = IsolationSandbox(
        sandbox_dir=temp_workspace / "sb1",
        network_policy=NetworkPolicy(allow_outbound=False),
    )
    allowed, msg = sandbox_disabled.validate_network_egress("https://pypi.org/simple")
    assert allowed is False
    assert "disabled" in msg.lower()

    # 2. Whitelisted network
    sandbox_enabled = IsolationSandbox(
        sandbox_dir=temp_workspace / "sb2",
        network_policy=NetworkPolicy(allow_outbound=True, allowed_domains={"pypi.org", "github.com"}),
    )
    allowed, _ = sandbox_enabled.validate_network_egress("https://pypi.org/project/fastapi")
    assert allowed is True

    allowed_evil, msg_evil = sandbox_enabled.validate_network_egress("https://attacker-c2.evil.com/leak")
    assert allowed_evil is False
    assert "not in the approved whitelist" in msg_evil


@pytest.mark.security
def test_sandbox_disk_quota(temp_workspace: Path):
    """Verify disk write quota checks."""
    sandbox = IsolationSandbox(
        sandbox_dir=temp_workspace,
        quota=ResourceQuota(max_disk_write_mb=10),
    )
    # 5 MB is fine
    allowed, _ = sandbox.validate_file_write_quota(5 * 1024 * 1024)
    assert allowed is True

    # 15 MB exceeds 10 MB quota
    allowed, msg = sandbox.validate_file_write_quota(15 * 1024 * 1024)
    assert allowed is False
    assert "exceeds quota" in msg


@pytest.mark.security
def test_privilege_guard_blocks_sensitive_files(temp_workspace: Path):
    """Verify PrivilegeGuard prevents tampering with security files or credentials."""
    guard = PrivilegeGuard(workspace_root=temp_workspace)

    # 1. Protected filename attempt
    allowed, msg = guard.is_access_permitted(temp_workspace / "vault.enc", mode="w")
    assert allowed is False
    assert "protected security file" in msg

    # 2. Attempt to write to .winai root
    allowed, msg = guard.is_access_permitted(temp_workspace / ".winai" / "security.json", mode="w")
    assert allowed is False

    # 3. Normal file write in workspace
    allowed, _ = guard.is_access_permitted(temp_workspace / "src" / "utils.py", mode="w")
    assert allowed is True


@pytest.mark.security
def test_anomaly_monitor_quarantine_and_bursts():
    """Verify anomaly monitor detects repeated failures and bursts, placing agent in quarantine."""
    monitor = AnomalyMonitor(failure_threshold=3, burst_window_seconds=1.0, max_burst=5)

    agent_id = "agt_rogue_01"
    session_id = "sess_01"

    # 1. Record 2 failures (below threshold)
    monitor.record_authorization_failure(agent_id, session_id, "Bad path")
    monitor.record_authorization_failure(agent_id, session_id, "Forbidden tool")
    assert monitor.is_quarantined(agent_id) is False

    # 2. Third failure triggers quarantine
    alert = monitor.record_authorization_failure(agent_id, session_id, "Traverse attempt")
    assert alert is not None
    assert alert.severity == "CRITICAL"
    assert monitor.is_quarantined(agent_id) is True

    # 3. Lift quarantine
    monitor.lift_quarantine(agent_id)
    assert monitor.is_quarantined(agent_id) is False
