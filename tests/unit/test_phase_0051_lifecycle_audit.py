"""PHASE 0051: startup wiring order is policy-first, registry-before-routes."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_startup_order_is_policy_first():
    text = (ROOT / "src" / "orchestrator" / "main.py").read_text(encoding="utf-8")
    policy_pos = text.index("SecurityPolicyEngine(")
    registry_pos = text.index("ProviderRegistry(")
    route_pos = text.index('@app.get("/health")')
    assert policy_pos < registry_pos < route_pos
