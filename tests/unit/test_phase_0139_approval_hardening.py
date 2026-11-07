"""PHASE 0139: forged, mismatched, and replayed nonces are denied."""
from src.security.approval_broker import ApprovalBroker


def _make(broker):
    return broker.create_request(
        tool_name="terminal_run",
        target_resource="pytest",
        parameters={"command": ["pytest"]},
        risk_tier="CRITICAL",
        reason="test",
        session_id="s9",
        agent_id="a9",
    )


def test_forged_nonce_denied():
    broker = ApprovalBroker()
    _make(broker)
    assert broker.verify_and_consume("0" * 64) is None
    assert broker.pending_count() == 1


def test_hash_mismatch_and_replay_denied():
    broker = ApprovalBroker()
    req = _make(broker)
    assert broker.verify_and_consume(req.nonce, expected_action_hash="wrong") is None
    assert broker.pending_count() == 1
    assert broker.verify_and_consume(req.nonce) is not None
    assert broker.verify_and_consume(req.nonce) is None
