"""PHASE 0045: filesystem area sign-off with write/read/rollback round-trip."""
from src.security.policy_engine import PolicyDecision, SecurityPolicyEngine
from src.windows_integration.file_service import ScopedFileService


def test_filesystem_area_signoff(tmp_path):
    svc = ScopedFileService(tmp_path)
    svc.write_file("notes/app.txt", "v1")
    assert svc.read_file("notes/app.txt") == "v1"
    bak = svc.write_file("notes/app.txt", "v2")
    assert svc.read_file("notes/app.txt") == "v2"
    assert svc.rollback(bak) is True
    assert svc.read_file("notes/app.txt") == "v1"

    policy = SecurityPolicyEngine(workspace_root=tmp_path, require_approvals=False)
    ok = policy.evaluate_action(
        tool_name="fs_read_file",
        arguments={"path": "notes/app.txt"},
        session_id="s",
        agent_id="a",
    )
    assert ok.decision == PolicyDecision.ALLOW
    denied = policy.evaluate_action(
        tool_name="fs_read_file",
        arguments={"path": "../../escape.txt"},
        session_id="s",
        agent_id="a",
    )
    assert denied.decision == PolicyDecision.DENY
