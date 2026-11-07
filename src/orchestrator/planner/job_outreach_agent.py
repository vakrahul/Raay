"""Trained Job & B2B Cold Outreach Agent.

Trained on proven B2B and sales outreach methodologies (Reddit r/sales frameworks):
1. Trigger-Based Demand Surge (CREIndiana Framework)
2. Pain-Point & Contrast Hook (SergenBalastic Framework 1)
3. Soft Follow-up / Value Bump (SergenBalastic Framework 2)
4. Clean Professional Break-up (SergenBalastic Framework 3)
5. Anti-Pattern Rule: Never use stale overused templates verbatim; anchor on real triggers.
"""

from pathlib import Path
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.orchestrator.brain.context_engine import AdvancedContextEngine, MemoryQualityTier
from src.orchestrator.brain.semantic_memory import SemanticMemory
from src.orchestrator.planner.agent_registry import AgentRegistry, AgentRole, SpecializedAgent


class ColdEmailFramework(str, Enum):
    TRIGGER_EVENT = "trigger_event"
    PAIN_POINT_CONTRAST = "pain_point_contrast"
    SOFT_FOLLOWUP = "soft_followup"
    CLEAN_BREAKUP = "clean_breakup"


class OutreachSpec(BaseModel):
    recipient_name: str = "Hiring Manager"
    recipient_company: str
    recipient_email: Optional[str] = None
    role_or_product: str
    trigger_event: Optional[str] = None
    pain_point: Optional[str] = None
    value_bullets: List[str] = Field(default_factory=list)
    framework: ColdEmailFramework = ColdEmailFramework.TRIGGER_EVENT
    sender_name: str = "Rahul Vakiti"
    sender_portfolio: str = "https://rahulvakiti.space"
    sender_email: str = "vakitirahul@gmail.com"
    sender_phone: str = "+91-7416754611"
    sender_resume_filename: str = "Rahul_vak_resume.pdf"


# Curated Knowledge Units from r/sales training data
TRAINING_KNOWLEDGE = [
    {
        "id": "cold_mail_trigger_framework",
        "title": "Trigger-Based Event Outreach (CREIndiana Model)",
        "content": (
            "Subject structure: 'Demand for [Domain] in light of [Trigger]'. "
            "Body starts with situational context: noticing surge in questions/demand regarding [Trigger]. "
            "Highlights response capabilities in 2-3 concise bullets. "
            "Closes with a low-friction, permission-based CTA: 'Would you be opposed if I...' or asking for feedback."
        ),
        "tags": ["cold_email", "trigger_event", "sales", "recruiter", "b2b"],
    },
    {
        "id": "cold_mail_pain_point_contrast",
        "title": "Pain-Point & Contrast Hook (SergenBalastic Model)",
        "content": (
            "Hook structure: 'If your [Current Metric/Pain] is higher than [Benchmark], we need to talk!'. "
            "Delivers a punchy 1-2 sentence solution statement. "
            "Avoids heavy demands; asks for a quick, focused conversation: 'How about a quick call to dive into some cool solutions?'."
        ),
        "tags": ["cold_email", "pain_point", "hook", "b2b", "recruiter"],
    },
    {
        "id": "cold_mail_soft_followup",
        "title": "Soft Follow-up & Inbox Bump",
        "content": (
            "Subject structure: 'Just floating this back to your inbox' or 'Following up on [Topic]'. "
            "Politely acknowledges that schedules get busy. "
            "Restates the core value alignment and offers a flexible, low-commitment check-in: "
            "'If it's easier, let me know what works best for you!'."
        ),
        "tags": ["cold_email", "follow_up", "polite_bump", "b2b", "job_search"],
    },
    {
        "id": "cold_mail_clean_breakup",
        "title": "Professional Clean Break-up Email",
        "content": (
            "Subject structure: 'Future opportunities for collaboration?'. "
            "Expresses high respect for the recipient's time: 'Since I haven't heard back, I'll assume your current priorities lie elsewhere, and I don't want to overstep by filling your inbox.' "
            "Leaves the door open warmly: 'If circumstances change, I'm only an email away.'"
        ),
        "tags": ["cold_email", "breakup_email", "closing", "b2b", "recruiter"],
    },
    {
        "id": "cold_mail_anti_patterns",
        "title": "Cold Email Anti-Patterns & Deliverability Rules",
        "content": (
            "Rule 1: Static templates found online get overused and outdated fast. Always dynamically personalize with real triggers. "
            "Rule 2: Never ask for a 30-minute commitment in the first touch. Use low-friction CTAs. "
            "Rule 3: Keep email body under 150 words. Mobile readers bounce on large paragraphs. "
            "Rule 4: Ground assertions in concrete evidence (links to live portfolio, research publications, or benchmark numbers)."
        ),
        "tags": ["cold_email", "best_practices", "anti_patterns", "deliverability"],
    },
]


class JobColdEmailAgent:
    """Trained agent for high-converting B2B and Job/Recruiter cold email generation."""

    def __init__(
        self,
        semantic_memory: Optional[SemanticMemory] = None,
        context_engine: Optional[AdvancedContextEngine] = None,
        workspace_root: Optional[Path] = None,
    ):
        self.workspace_root = workspace_root or Path.cwd()
        self.semantic_memory = semantic_memory or SemanticMemory()
        self.context_engine = context_engine or AdvancedContextEngine(workspace_root=self.workspace_root)
        self.is_trained = False
        self._train_agent()

    def _train_agent(self) -> None:
        """Ingest the r/sales knowledge into the semantic brain tiers."""
        for item in TRAINING_KNOWLEDGE:
            # Store in semantic vector memory
            self.semantic_memory.store_fact(
                fact_id=item["id"],
                content=f"{item['title']}: {item['content']}",
                tags=item["tags"],
            )
            # Store in context engine provenance base
            self.context_engine.add_knowledge_item(
                content=f"{item['title']} -> {item['content']}",
                quality_tier=MemoryQualityTier.VERIFIED_FACT,
                source="r/sales_training_corpus",
                confidence=0.98,
            )
        self.is_trained = True

    def generate_email(self, spec: OutreachSpec) -> Dict[str, str]:
        """Generate a structured cold email adhering to the trained sales frameworks."""
        if spec.framework == ColdEmailFramework.TRIGGER_EVENT:
            return self._build_trigger_email(spec)
        elif spec.framework == ColdEmailFramework.PAIN_POINT_CONTRAST:
            return self._build_pain_point_email(spec)
        elif spec.framework == ColdEmailFramework.SOFT_FOLLOWUP:
            return self._build_soft_followup(spec)
        elif spec.framework == ColdEmailFramework.CLEAN_BREAKUP:
            return self._build_clean_breakup(spec)
        else:
            return self._build_trigger_email(spec)

    def _build_trigger_email(self, spec: OutreachSpec) -> Dict[str, str]:
        trigger = spec.trigger_event or f"recent growth and engineering initiatives at {spec.recipient_company}"
        subject = f"Demand for {spec.role_or_product} in light of {trigger[:35]}"

        bullets = spec.value_bullets or [
            f"Autonomous Agent Systems: Built production agentic execution pipelines and prompt defense frameworks.",
            f"Research Publications: Authored research on Transformer data leakage and explanation faithfulness in IRE Journals.",
            f"Startup Experience: Scaled real-time workflows and model pipelines as SDE intern at YC-backed / AI startups.",
        ]
        bullet_str = "\n".join(f"• {b}" for b in bullets)

        body = (
            f"Hi {spec.recipient_name},\n\n"
            f"I came across {spec.recipient_company}'s focus on {trigger} and wanted to reach out.\n\n"
            f"In response to recent demand spikes in {spec.role_or_product}, I have been actively building:\n"
            f"{bullet_str}\n\n"
            f"Portfolio: {spec.sender_portfolio}\n"
            f"My resume ({spec.sender_resume_filename}) is attached for reference.\n\n"
            f"Would you be opposed to a brief 5-minute sync this week to explore if my background aligns with your team's goals?\n\n"
            f"Best regards,\n"
            f"{spec.sender_name}\n"
            f"{spec.sender_email} | {spec.sender_phone}"
        )
        return {"subject": subject, "body": body, "framework": spec.framework.value}

    def _build_pain_point_email(self, spec: OutreachSpec) -> Dict[str, str]:
        pain = spec.pain_point or "engineering overhead in scaling autonomous workflows"
        subject = f"Quick question regarding {pain[:30]} at {spec.recipient_company}"

        body = (
            f"Hi {spec.recipient_name},\n\n"
            f"If {pain} is taking more time than your team's core product roadmap, we should talk!\n\n"
            f"I'm {spec.sender_name}. I specialize in building zero-trust autonomous AI architectures, "
            f"high-assurance agents, and self-healing systems that eliminate execution friction.\n\n"
            f"Highlights & live proof of work:\n"
            f"• Portfolio & Projects: {spec.sender_portfolio}\n"
            f"• Published research on reliable model architectures in IRE Journals.\n\n"
            f"How about a quick call this week to share some ideas that might help {spec.recipient_company}?\n\n"
            f"Warm regards,\n"
            f"{spec.sender_name}\n"
            f"{spec.sender_email}"
        )
        return {"subject": subject, "body": body, "framework": spec.framework.value}

    def _build_soft_followup(self, spec: OutreachSpec) -> Dict[str, str]:
        subject = f"Just floating this back to your inbox: {spec.role_or_product}"

        body = (
            f"Hi {spec.recipient_name},\n\n"
            f"I wanted to touch base on my previous note regarding {spec.role_or_product} at {spec.recipient_company}. "
            f"Have you had a chance to consider how my engineering background might fit into your current roadmap?\n\n"
            f"I understand how busy schedules get. If it's easier, I'm available for a quick 5-minute check-in this week—let me know what works best for you!\n\n"
            f"Portfolio: {spec.sender_portfolio}\n\n"
            f"Looking forward to hearing from you,\n"
            f"{spec.sender_name}"
        )
        return {"subject": subject, "body": body, "framework": spec.framework.value}

    def _build_clean_breakup(self, spec: OutreachSpec) -> Dict[str, str]:
        subject = f"Future opportunities for collaboration? — {spec.recipient_company}"

        body = (
            f"Hi {spec.recipient_name},\n\n"
            f"I've reached out previously regarding how I can contribute to {spec.recipient_company}'s engineering goals in {spec.role_or_product}. "
            f"Since I haven't heard back, I'll assume your current priorities lie elsewhere, and I don't want to overstep by filling your inbox.\n\n"
            f"If circumstances change or a relevant opportunity opens up, I'm only an email away ({spec.sender_email}).\n\n"
            f"I hope there's a chance for us to collaborate in the future. Wishing you and {spec.recipient_company} continued success!\n\n"
            f"Best regards,\n"
            f"{spec.sender_name}\n"
            f"{spec.sender_portfolio}"
        )
        return {"subject": subject, "body": body, "framework": spec.framework.value}


def register_job_outreach_agent(registry: AgentRegistry) -> None:
    """Register the specialized Job & B2B Outreach Agent into the system registry."""
    agent = SpecializedAgent(
        role=AgentRole.RESEARCHER,  # Compatible domain role
        agent_id="agent_job_outreach_specialist",
        display_name="Job & B2B Cold Outreach Specialist",
        system_prompt=(
            "You are an expert B2B and Job Outreach Agent trained on high-converting sales frameworks. "
            "You avoid overused generic templates and instead write trigger-based, pain-point-contrast, "
            "and low-friction cold emails with clear proof of work and concise CTAs."
        ),
        authorized_tools=["browser_open_x", "fs_read_file", "fs_write_file"],
        max_tokens=2048,
        max_duration_seconds=30,
        verification_requirements="Generated email must include verified trigger, proof links, and low-friction CTA",
    )
    registry.register_agent(agent)
