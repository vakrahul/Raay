"""PHASE 0074: error paths fail closed without leaking internals."""
from src.orchestrator.errors import WinAIError


def test_error_str_never_embeds_traceback():
    err = WinAIError("E_TEST", "safe message", "safe remediation")
    assert "Traceback" not in str(err)
    assert err.to_dict()["remediation"] == "safe remediation"


def test_base_is_catchable_as_exception():
    try:
        raise WinAIError("E_X", "x")
    except Exception as e:
        assert isinstance(e, WinAIError)
    else:
        raise AssertionError("WinAIError was not raised")
