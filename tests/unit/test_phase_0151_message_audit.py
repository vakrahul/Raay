"""PHASE 0151: message contract shapes exist with strict roles."""
from src.providers.base import ChatMessage, ProviderResponse, ToolCallProposal


def test_message_contract_shapes():
    msg = ChatMessage(role="tool", content="result", tool_call_id="call_1")
    assert msg.tool_call_id == "call_1"
    tc = ToolCallProposal(id="call_1", tool_name="fs_read_file", arguments={"path": "x"})
    resp = ProviderResponse(content="done", model_name="mock", tool_calls=[tc])
    assert resp.finish_reason == "stop"
    assert resp.usage == {}
