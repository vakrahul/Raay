"""PHASE 0184: display summaries never leak nonces or parameters."""
from src.security.approval_broker import ApprovalBroker


def test_describe_never_leaks_secrets():
    broker = ApprovalBroker()
    req = broker.create_request(
        tool_name="terminal_run",
        target_resource="pytest",
        parameters={"command": ["pytest"], "token": "abc123"},
        risk_tier="CRITICAL",
        reason="run tests",
        session_id="s",
        agent_id="a",
    )
    summary = broker.describe_request(req.nonce)
    blob = str(summary)
    assert req.nonce not in blob
    assert "abc123" not in blob
