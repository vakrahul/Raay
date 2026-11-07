"""PHASE 0100: STAGE 01 sign-off — roadmap stage 1 holds 100 phases."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_stage01_holds_100_phases():
    payload = json.loads((ROOT / "docs" / "MASTER_ROADMAP_1000_PHASES.json").read_text(encoding="utf-8"))
    stage01 = [p for p in payload["phases"] if p["stage_number"] == 1]
    assert len(stage01) == 100
    assert stage01[0]["phase_id"] == "PHASE 0001"
    assert stage01[-1]["phase_id"] == "PHASE 0100"
