"""PHASE 0001 baseline: roadmap integrity plus critical-module importability."""
import json
from pathlib import Path


def test_roadmap_has_exactly_1000_sequenced_phases():
    roadmap_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "MASTER_ROADMAP_1000_PHASES.json"
    )
    assert roadmap_path.exists(), "master roadmap JSON is missing"
    payload = json.loads(roadmap_path.read_text(encoding="utf-8"))
    phases = payload["phases"]
    assert len(phases) == 1000
    assert phases[0]["phase_id"] == "PHASE 0001"
    assert phases[-1]["phase_id"] == "PHASE 1000"
    assert len({p["phase_id"] for p in phases}) == 1000
    assert len({p["title"] for p in phases}) == 1000
    assert payload.get("stages") == 10
    for stage_no in range(1, 11):
        stage_phases = [p for p in phases if p["stage_number"] == stage_no]
        assert len(stage_phases) == 100


def test_required_roadmap_tracking_docs_exist():
    docs_dir = Path(__file__).resolve().parents[2] / "docs"
    for name in (
        "MASTER_ROADMAP_1000_PHASES.md",
        "ROADMAP_DEPENDENCIES.md",
        "PHASE_COMPLETION_POLICY.md",
        "IMPLEMENTATION_STATUS.md",
        "PHASE_0001_REPORT.md",
    ):
        assert (docs_dir / name).exists(), f"missing required doc: {name}"


def test_critical_modules_import_without_side_effects():
    import src.orchestrator.config as config_module
    import src.providers.base as providers_base
    import src.security.policy_engine as policy_engine

    assert hasattr(config_module, "AppConfig")
    assert hasattr(providers_base, "BaseModelProvider")
    assert hasattr(policy_engine, "SecurityPolicyEngine")
