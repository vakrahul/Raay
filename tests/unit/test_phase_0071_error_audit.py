"""PHASE 0071: fail-closed error paths exist in endpoints and policy."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_fail_closed_markers_present():
    main = (ROOT / "src" / "orchestrator" / "main.py").read_text(encoding="utf-8")
    assert "HTTPException" in main and "404" in main
    policy = (ROOT / "src" / "security" / "policy_engine.py").read_text(encoding="utf-8")
    assert "DENY" in policy and "REQUIRE_APPROVAL" in policy
