"""PHASE 0154: message shapes reject non-dict arguments and blank roles."""
import pytest
from pydantic import ValidationError

from src.providers.base import ChatMessage, ToolCallProposal


def test_tool_arguments_must_be_dict():
    with pytest.raises(ValidationError):
        ToolCallProposal(id="c1", tool_name="fs_read_file", arguments="path=x")


def test_blank_and_padded_roles_rejected():
    for role in ("", " ", "User", " assistant"):
        with pytest.raises(ValidationError):
            ChatMessage(role=role, content="hi")
