"""PHASE 0155: message area sign-off with assistant tool-call flow."""
from src.providers.base import ChatMessage, ProviderResponse, ToolCallProposal


def test_message_area_signoff():
    history = [
        ChatMessage(role="system", content="You are WinAI."),
        ChatMessage(role="user", content="List files"),
    ]
    tc = ToolCallProposal(id="call_9", tool_name="fs_list_files", arguments={"path": "."})
    resp = ProviderResponse(content="I will list files.", model_name="mock", tool_calls=[tc])
    assert resp.has_tool_calls is True
    followup = ChatMessage(role="tool", content="a.txt", tool_call_id="call_9")
    assert followup.tool_call_id == "call_9"
    assert len(history) == 2
