"""PHASE 0185: approval UX area sign-off with describe/consume flow."""
from src.security.approval_broker import ApprovalBroker


def test_ux_area_signoff():
    broker = ApprovalBroker()
    req = broker.create_request(
        tool_name="deploy_project",
        target_resource="preview://demo",
        parameters={},
        risk_tier="CRITICAL",
        reason="publish preview",
        session_id="sess_ux",
        agent_id="agt_deployer",
    )
    assert broker.describe_request(req.nonce)["risk_tier"] == "CRITICAL"
    assert broker.pending_count("sess_ux") == 1
    assert broker.verify_and_consume(req.nonce) is not None
    assert broker.describe_request(req.nonce) is None
