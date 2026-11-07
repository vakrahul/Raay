"""Integration tests for Phase 10 Initial Vertical Slice."""
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from src.orchestrator.config import AppConfig, SecurityLevel
from src.orchestrator.main import create_app
from src.security.policy_engine import PolicyDecision, RiskTier


@pytest.fixture
def client_app(temp_workspace: Path):
    """TestClient wired to an isolated workspace."""
    cfg = AppConfig(
        workspace_root=temp_workspace,
        security_level=SecurityLevel.STRICT,
        environment="testing",
    )
    application = create_app(cfg)
    with TestClient(application) as client:
        yield client, cfg


@pytest.mark.integration
def test_health_check_endpoint(client_app):
    """Verify orchestrator health check and mock provider readiness."""
    client, _ = client_app
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["provider_healthy"] is True


@pytest.mark.integration
def test_system_config_endpoint(client_app):
    """Verify system config exposure over REST."""
    client, cfg = client_app
    response = client.get("/api/v1/config")
    assert response.status_code == 200
    data = response.json()
    assert data["security_level"] == "strict"
    assert data["workspace_root"] == str(cfg.workspace_root)


@pytest.mark.integration
def test_chat_completion_flow(client_app):
    """Verify request/response cycle through mock AI provider."""
    client, _ = client_app
    payload = {
        "messages": [
            {"role": "user", "content": "Hello AI environment"}
        ],
        "temperature": 0.5,
    }
    response = client.post("/api/v1/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "Mock response to: 'Hello AI environment'" in data["content"]
    assert data["model_name"] == "mock-gpt-4o"

    # Second identical request should hit cache and update telemetry
    response2 = client.post("/api/v1/chat", json=payload)
    assert response2.status_code == 200

    telemetry_resp = client.get("/api/v1/telemetry/tokens")
    assert telemetry_resp.status_code == 200
    telemetry = telemetry_resp.json()
    assert "token_accounting" in telemetry
    assert "financial_summary" in telemetry
    assert "savings_overview" in telemetry
    assert telemetry["token_accounting"]["total_requests"] >= 1


@pytest.mark.integration
def test_path_confinement_security(client_app):
    """Verify policy engine blocks path traversal attempts from untrusted tool calls."""
    client, cfg = client_app
    from src.security.policy_engine import SecurityPolicyEngine

    policy = SecurityPolicyEngine(workspace_root=cfg.workspace_root)

    # 1. Allowed operation inside workspace
    allow_result = policy.evaluate_action(
        tool_name="fs_list_files",
        arguments={"path": "."},
        session_id="test_sess",
        agent_id="test_agt",
    )
    assert allow_result.decision == PolicyDecision.ALLOW

    # 2. Blocked path traversal attempting to escape workspace
    deny_result = policy.evaluate_action(
        tool_name="fs_read_file",
        arguments={"path": "../../Windows/System32/config/SAM"},
        session_id="test_sess",
        agent_id="test_agt",
    )
    assert deny_result.decision == PolicyDecision.DENY
    assert deny_result.risk_tier == RiskTier.CRITICAL
    assert "escapes workspace" in deny_result.reason or "forbidden system directory" in deny_result.reason


@pytest.mark.integration
def test_human_approval_lifecycle(client_app):
    """Verify tool action requiring approval generates nonce, consumes once, and rejects replay."""
    client, _ = client_app

    # Trigger write action through policy
    from src.security.policy_engine import SecurityPolicyEngine
    policy = SecurityPolicyEngine(workspace_root=Path.cwd(), require_approvals=True)

    result = policy.evaluate_action(
        tool_name="fs_write_file",
        arguments={"path": "test_output.txt", "content": "hello"},
        session_id="s1",
        agent_id="a1",
    )
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    assert result.approval_nonce is not None

    nonce = result.approval_nonce
    consumed = policy.consume_approval(nonce)
    assert consumed is not None
    assert consumed["tool_name"] == "fs_write_file"

    # Replay attempt must return None
    replayed = policy.consume_approval(nonce)
    assert replayed is None


@pytest.mark.integration
def test_approval_pending_endpoint_end_to_end(client_app):
    """Verify the on-screen button's API loop: proposal -> pending -> approve -> cleared."""
    client, _ = client_app

    assert client.get("/api/v1/approval/pending").json() == {"pending": []}

    with client.websocket_connect("/ws/v1/stream") as websocket:
        websocket.send_json({
            "action": "chat",
            "messages": [{"role": "user", "content": "Please run a command"}]
        })
        nonce = None
        while True:
            msg = websocket.receive_json()
            if msg.get("event") == "tool_proposal":
                assert msg["tool_call"]["tool_name"] == "terminal_run"
                assert msg["policy_decision"]["decision"] == "REQUIRE_APPROVAL"
                nonce = msg["policy_decision"]["approval_nonce"]
            elif msg.get("event") == "done":
                break
        assert nonce, "strict policy must issue a nonce for terminal_run"

    pending = client.get("/api/v1/approval/pending").json()["pending"]
    assert len(pending) == 1
    assert pending[0]["approval_nonce"] == nonce
    assert pending[0]["tool_name"] == "terminal_run"

    resp = client.post("/api/v1/approval/respond", json={
        "approval_nonce": nonce, "user_decision": "APPROVED",
    })
    assert resp.status_code == 200
    assert client.get("/api/v1/approval/pending").json() == {"pending": []}

    replay = client.post("/api/v1/approval/respond", json={
        "approval_nonce": nonce, "user_decision": "APPROVED",
    })
    assert replay.status_code == 404


@pytest.mark.integration
def test_websocket_streaming_and_tool_proposal(client_app):
    """Verify WebSocket real-time token streaming and tool proposal event dispatch."""
    client, _ = client_app

    with client.websocket_connect("/ws/v1/stream") as websocket:
        # Send chat command triggering mock tool call
        websocket.send_json({
            "action": "chat",
            "messages": [{"role": "user", "content": "Please list files in workspace"}]
        })

        tokens = []
        tool_proposals = []
        completed = False

        while not completed:
            msg = websocket.receive_json()
            event = msg.get("event")
            if event == "token":
                tokens.append(msg["data"])
            elif event == "tool_proposal":
                tool_proposals.append(msg)
            elif event == "done":
                completed = True

        assert len(tokens) > 0
        assert "".join(tokens).startswith("This is a secure streamed response")
        assert len(tool_proposals) == 1
        assert tool_proposals[0]["tool_call"]["tool_name"] == "fs_list_files"
        assert tool_proposals[0]["policy_decision"]["decision"] == "ALLOW"
