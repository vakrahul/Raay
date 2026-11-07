"""PHASE 0153: tool-call presence helper branches dispatch correctly."""
from src.providers.base import ProviderResponse, ToolCallProposal


def test_has_tool_calls_flag():
    plain = ProviderResponse(content="hi", model_name="mock")
    assert plain.has_tool_calls is False
    tc = ToolCallProposal(id="c1", tool_name="fs_read_file", arguments={"path": "x"})
    routed = ProviderResponse(content="", model_name="mock", tool_calls=[tc])
    assert routed.has_tool_calls is True
