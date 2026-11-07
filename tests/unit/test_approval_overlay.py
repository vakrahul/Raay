"""Unit tests for the on-screen approval button (client logic only, no display)."""
import httpx
import pytest

from src.providers.base import ChatMessage
from src.providers.mock_provider import MockProvider
from src.security.policy_engine import PolicyDecision, SecurityPolicyEngine
from src.windows_integration.approval_overlay import ApprovalServiceClient


def _transport(routes):
    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        assert key in routes, f"unexpected call {key}"
        status, payload = routes[key]
        return httpx.Response(status, json=payload)
    return httpx.MockTransport(handler)


@pytest.mark.unit
def test_client_lists_and_decides():
    routes = {
        ("GET", "/api/v1/approval/pending"): (200, {"pending": [
            {"approval_nonce": "n1", "tool_name": "terminal_run",
             "target": "['echo']", "session_id": "s"},
        ]}),
        ("POST", "/api/v1/approval/respond"): (200, {"status": "processed"}),
    }
    client = ApprovalServiceClient(transport=_transport(routes))
    pending = client.get_pending()
    assert len(pending) == 1
    assert pending[0].approval_nonce == "n1"
    assert pending[0].tool_name == "terminal_run"
    assert client.respond("n1", "APPROVED") is True
    assert client.respond("n1", "MAYBE") is False
    client.close()


@pytest.mark.unit
def test_client_offline_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")
    client = ApprovalServiceClient(transport=httpx.MockTransport(handler))
    assert client.get_pending() == []
    assert client.respond("n1", "DENIED") is False
    client.close()


@pytest.mark.unit
def test_policy_summary_lists_pending_nonce(tmp_path):
    policy = SecurityPolicyEngine(workspace_root=tmp_path, require_approvals=True)
    assert policy.pending_approvals_summary() == []
    result = policy.evaluate_action(
        tool_name="terminal_run",
        arguments={"command": ["echo", "hi"]},
        session_id="s1",
        agent_id="a1",
    )
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    summary = policy.pending_approvals_summary()
    assert len(summary) == 1
    assert summary[0]["approval_nonce"] == result.approval_nonce
    assert summary[0]["tool_name"] == "terminal_run"


@pytest.mark.asyncio
async def test_mock_run_command_trigger():
    resp = await MockProvider().complete([ChatMessage(role="user", content="Please run a command")])
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].tool_name == "terminal_run"
