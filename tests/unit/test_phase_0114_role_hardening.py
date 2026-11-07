"""PHASE 0114: hostile chat roles fail validation closed."""
import pytest
from pydantic import ValidationError

from src.providers.base import ChatMessage


def test_hostile_roles_rejected():
    for role in ("admin", "SYSTEM", "", "user ", "assistant\x00"):
        with pytest.raises(ValidationError):
            ChatMessage(role=role, content="hi")


def test_valid_roles_accepted():
    for role in ("system", "user", "assistant", "tool"):
        assert ChatMessage(role=role, content="hi").role == role
