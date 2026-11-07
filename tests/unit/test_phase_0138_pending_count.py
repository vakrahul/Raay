"""PHASE 0138: pending approval counts track issuance and consumption."""
from src.security.approval_broker import ApprovalBroker


def _make(broker, session="s1"):
    return broker.create_request(
        tool_name="fs_write_file",
        target_resource="/tmp/x.txt",
        parameters={"path": "/tmp/x.txt"},
        risk_tier="HIGH",
        reason="test",
        session_id=session,
        agent_id="a1",
    )


def test_pending_count_lifecycle():
    broker = ApprovalBroker()
    assert broker.pending_count() == 0
    req = _make(broker)
    assert broker.pending_count() == 1
    assert broker.pending_count("s1") == 1
    assert broker.pending_count("other") == 0
    broker.verify_and_consume(req.nonce)
    assert broker.pending_count() == 0
