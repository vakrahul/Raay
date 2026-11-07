"""Unit tests for Trained Job & B2B Cold Outreach Agent."""
from pathlib import Path
import pytest
from src.orchestrator.brain.context_engine import AdvancedContextEngine, MemoryQualityTier
from src.orchestrator.brain.semantic_memory import SemanticMemory
from src.orchestrator.planner.agent_registry import AgentRegistry
from src.orchestrator.planner.job_outreach_agent import (
    ColdEmailFramework,
    JobColdEmailAgent,
    OutreachSpec,
    register_job_outreach_agent,
)


@pytest.mark.unit
def test_agent_training_and_semantic_ingestion(tmp_path: Path):
    """Verify that the r/sales knowledge is ingested into semantic memory and context engine."""
    semantic = SemanticMemory()
    context = AdvancedContextEngine(workspace_root=tmp_path)
    agent = JobColdEmailAgent(semantic_memory=semantic, context_engine=context, workspace_root=tmp_path)

    assert agent.is_trained is True

    # 1. Semantic memory verification
    hits = semantic.retrieve_similar("trigger event demand spike", top_k=3)
    assert len(hits) > 0
    top_entry, score = hits[0]
    assert "Trigger-Based" in top_entry.content or "cold_email" in top_entry.tags

    # 2. Context engine verification
    items = context.retrieve_relevant_context("breakup email priorities lie elsewhere")
    assert len(items) > 0
    assert items[0].quality_tier == MemoryQualityTier.VERIFIED_FACT


@pytest.mark.unit
def test_trigger_event_email_generation():
    """Verify Trigger-Based outreach email generation."""
    agent = JobColdEmailAgent()
    spec = OutreachSpec(
        recipient_name="Alex",
        recipient_company="OxAstra",
        role_or_product="AI/ML Research Intern",
        trigger_event="recent expansion into edge vision models",
        framework=ColdEmailFramework.TRIGGER_EVENT,
    )
    email = agent.generate_email(spec)
    assert "Demand for AI/ML Research Intern" in email["subject"]
    assert "Would you be opposed" in email["body"]
    assert "https://rahulvakiti.space" in email["body"]
    assert "Rahul_vak_resume.pdf" in email["body"]


@pytest.mark.unit
def test_pain_point_contrast_email_generation():
    """Verify Pain-Point and Contrast hook outreach email generation."""
    agent = JobColdEmailAgent()
    spec = OutreachSpec(
        recipient_name="Sarah",
        recipient_company="TechCorp",
        role_or_product="Autonomous Agent Infrastructure",
        pain_point="manual prompt engineering and workflow fragility",
        framework=ColdEmailFramework.PAIN_POINT_CONTRAST,
    )
    email = agent.generate_email(spec)
    assert "Quick question regarding" in email["subject"]
    assert "we should talk!" in email["body"]
    assert "https://rahulvakiti.space" in email["body"]


@pytest.mark.unit
def test_soft_followup_and_clean_breakup():
    """Verify follow-up and clean break-up generation."""
    agent = JobColdEmailAgent()

    # Soft Followup
    spec_followup = OutreachSpec(
        recipient_name="David",
        recipient_company="SaaSFlow",
        role_or_product="Backend Engineering",
        framework=ColdEmailFramework.SOFT_FOLLOWUP,
    )
    followup = agent.generate_email(spec_followup)
    assert "Just floating this back to your inbox" in followup["subject"]
    assert "I understand how busy schedules get" in followup["body"]

    # Clean Breakup
    spec_breakup = OutreachSpec(
        recipient_name="David",
        recipient_company="SaaSFlow",
        role_or_product="Backend Engineering",
        framework=ColdEmailFramework.CLEAN_BREAKUP,
    )
    breakup = agent.generate_email(spec_breakup)
    assert "Future opportunities for collaboration?" in breakup["subject"]
    assert "priorities lie elsewhere" in breakup["body"]
    assert "only an email away" in breakup["body"]


@pytest.mark.unit
def test_registry_integration():
    """Verify registration into the system's AgentRegistry."""
    registry = AgentRegistry()
    register_job_outreach_agent(registry)
    agent = registry.get_agent("agent_job_outreach_specialist")
    assert agent is not None
    assert agent.display_name == "Job & B2B Cold Outreach Specialist"
