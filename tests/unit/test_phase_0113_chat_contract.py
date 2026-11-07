"""PHASE 0113: C#/Python chat contracts mirror each other."""
from scripts.check_chat_contract import check_chat_contract


def test_chat_contract_mirrors():
    assert check_chat_contract() == []
