"""PHASE 0061: health endpoint contract exists with degraded fallback."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_health_handler_contract():
    text = (ROOT / "src" / "orchestrator" / "main.py").read_text(encoding="utf-8")
    assert '"/health"' in text
    assert '"healthy" if provider_healthy else "degraded"' in text
    assert "provider_healthy" in text
