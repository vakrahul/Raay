"""PHASE 0140: approval area sign-off with session revocation."""
from src.security.approval_broker import ApprovalBroker


def test_approval_area_signoff():
    broker = ApprovalBroker()
    for _ in range(2):
        broker.create_request(
            tool_name="fs_write_file",
            target_resource="/tmp/y.txt",
            parameters={"path": "/tmp/y.txt"},
            risk_tier="HIGH",
            reason="signoff",
            session_id="sess_sign",
            agent_id="a1",
        )
    assert broker.pending_count("sess_sign") == 2
    assert broker.revoke_all_for_session("sess_sign") == 2
    assert broker.pending_count() == 0
