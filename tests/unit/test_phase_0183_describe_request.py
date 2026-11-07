"""PHASE 0183: display summary exposes action fields without secrets."""
from src.security.approval_broker import ApprovalBroker


def test_describe_request_safe_fields():
    broker = ApprovalBroker()
    req = broker.create_request(
        tool_name="fs_write_file",
        target_resource="/tmp/z.txt",
        parameters={"path": "/tmp/z.txt", "api_key": "shh"},
        risk_tier="HIGH",
        reason="user review",
        session_id="s",
        agent_id="a",
    )
    summary = broker.describe_request(req.nonce)
    assert summary == {
        "tool_name": "fs_write_file",
        "target_resource": "/tmp/z.txt",
        "risk_tier": "HIGH",
        "reason": "user review",
    }
    assert broker.describe_request("0" * 64) is None
