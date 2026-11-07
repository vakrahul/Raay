"""Adversarial Security Audit Test Suite (Phase 60)."""
from pathlib import Path
import pytest

from src.security.action_validator import ActionValidator
from src.security.approval_broker import ApprovalBroker
from src.security.emergency_controls import EmergencyController
from src.security.permission_model import PermissionManager, PermissionScope
from src.security.policy_engine import PolicyDecision, SecurityPolicyEngine


@pytest.mark.security
def test_path_traversal_and_system_jail(temp_workspace: Path):
    """Verify adversarial path traversal and system directory targets are blocked."""
    policy = SecurityPolicyEngine(workspace_root=temp_workspace)

    malicious_targets = [
        "../../Windows/System32/cmd.exe",
        "C:/Windows/System32/calc.exe",
        "../" * 10 + "boot.ini",
        "file.txt::$DATA",
        "C:\\Program Files\\app.exe",
    ]

    for target in malicious_targets:
        res = policy.evaluate_action(
            tool_name="fs_read_file",
            arguments={"path": target},
            session_id="sec_test",
            agent_id="agt_adversary",
        )
        assert res.decision == PolicyDecision.DENY, f"Target '{target}' should have been DENIED"


@pytest.mark.security
def test_action_validator_null_byte_injection():
    """Verify ActionValidator detects and blocks null byte injection attacks."""
    null_byte_payload = {
        "path": "innocent_doc.txt\x00.exe",
        "content": "payload",
    }
    is_valid, _, msg = ActionValidator.validate_action("fs_write_file", null_byte_payload)
    assert is_valid is False
    assert "Null byte detected" in msg


@pytest.mark.security
def test_action_validator_extra_field_tampering():
    """Verify ActionValidator rejects payloads with extra unrecognized fields."""
    tampered_payload = {
        "path": "test.txt",
        "content": "hello",
        "escalate_privilege": True, # Unauthorized extra field
    }
    is_valid, _, msg = ActionValidator.validate_action("fs_write_file", tampered_payload)
    assert is_valid is False
    assert "extra" in msg.lower() or "validation failure" in msg.lower()


@pytest.mark.security
def test_approval_nonce_forgery_and_replay():
    """Verify approval broker rejects forged nonces and single-use replay attacks."""
    broker = ApprovalBroker()

    # Create legitimate request
    req = broker.create_request(
        tool_name="fs_write_file",
        target_resource="D:/test.txt",
        parameters={"path": "D:/test.txt"},
        risk_tier="HIGH",
        reason="Test write",
        session_id="s1",
        agent_id="a1",
    )

    # 1. Forged nonce attempt
    forged = broker.verify_and_consume("0123456789abcdef" * 4)
    assert forged is None

    # 2. Action hash mismatch attempt (attacker swapped action)
    mismatched = broker.verify_and_consume(req.nonce, expected_action_hash="fake_hash_123")
    assert mismatched is None

    # 3. Legitimate consumption
    valid = broker.verify_and_consume(req.nonce, expected_action_hash=req.action_hash)
    assert valid is not None
    assert valid.tool_name == "fs_write_file"

    # 4. Replay attempt
    replay = broker.verify_and_consume(req.nonce)
    assert replay is None, "Replay of consumed nonce must be rejected"


@pytest.mark.security
def test_permission_revocation_and_taint(temp_workspace: Path):
    """Verify that permission manager revokes agents and downgrades tainted agents."""
    pm = PermissionManager(workspace_root=temp_workspace)
    agent = pm.create_agent_identity(role="coder")

    target = str(temp_workspace / "main.py")

    # Initially granted
    granted, req_appr = pm.check_permission(agent.agent_id, PermissionScope.FS_READ, target)
    assert granted is True
    assert req_appr is False

    # Taint agent with untrusted input
    pm.taint_agent(agent.agent_id)
    granted, req_appr = pm.check_permission(agent.agent_id, PermissionScope.FS_READ, target)
    assert granted is True
    assert req_appr is True, "Tainted agent must be forced to require approval"

    # Revoke agent
    pm.revoke_agent(agent.agent_id)
    granted, _ = pm.check_permission(agent.agent_id, PermissionScope.FS_READ, target)
    assert granted is False, "Revoked agent must be denied all permissions"


@pytest.mark.security
def test_emergency_controller_kill_switch():
    """Verify emergency controller halt state."""
    ctrl = EmergencyController()
    assert ctrl.is_halted is False

    res = ctrl.trigger_emergency_stop()
    assert ctrl.is_halted is True
    assert res["status"] == "HALTED"
    assert ctrl.is_session_paused("any_session") is True

    ctrl.reset_emergency_stop()
    assert ctrl.is_halted is False
