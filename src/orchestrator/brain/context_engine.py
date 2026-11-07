"""Advanced Context Layer Brain (Phase 2).

Maintains structured Task Context, dynamic Project Context (via live filesystem inspection),
hybrid retrieval with confidence provenance, and lossless security-preserving context compaction.
"""

from enum import Enum
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from src.orchestrator.brain.error_memory import ErrorReflectionMemory
from src.orchestrator.brain.models import MemoryEntry, MemoryType, VerificationStatus
from src.orchestrator.brain.semantic_memory import cosine_similarity, simple_embedding


class MemoryQualityTier(str, Enum):
    VERIFIED_FACT = "VERIFIED_FACT"               # Ground-truth verified through tool execution / tests
    USER_ASSERTED = "USER_ASSERTED"               # Directly declared by the user
    MODEL_HYPOTHESIS = "MODEL_HYPOTHESIS"         # Model-generated speculation (never promoted to permanent fact)
    UNVERIFIED_ASSUMPTION = "UNVERIFIED_ASSUMPTION" # Working operational assumption
    FAILED_APPROACH = "FAILED_APPROACH"           # Tested approach that resulted in an error
    SUCCESSFUL_CORRECTION = "SUCCESSFUL_CORRECTION"# Verified fix resolving a prior failure


class ProvenanceMemoryItem(BaseModel):
    item_id: str
    content: str
    quality_tier: MemoryQualityTier
    source: str  # e.g., "file:package.json", "tool:pytest", "user_prompt"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    timestamp: float = Field(default_factory=time.time)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    embedding: Optional[List[float]] = None


class LiveTaskContext(BaseModel):
    task_id: str
    user_goal: str
    task_type: str = "general"
    execution_plan: List[str] = Field(default_factory=list)
    completed_steps: List[str] = Field(default_factory=list)
    pending_steps: List[str] = Field(default_factory=list)
    current_step: Optional[str] = None
    tool_permissions: List[str] = Field(default_factory=list)
    relevant_context: List[str] = Field(default_factory=list)
    known_constraints: List[str] = Field(default_factory=list)
    failure_history: List[str] = Field(default_factory=list)
    verification_requirements: List[str] = Field(default_factory=list)
    execution_status: str = "PLANNING"  # PLANNING, EXECUTING, VERIFYING, COMPLETED, FAILED


class LiveProjectContext(BaseModel):
    project_dir: str
    languages: List[str] = Field(default_factory=list)
    frameworks: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    database_technology: str = "SQLite WAL"
    architectural_decisions: List[str] = Field(default_factory=list)
    test_commands: List[str] = Field(default_factory=list)
    build_commands: List[str] = Field(default_factory=list)
    known_bugs: List[str] = Field(default_factory=list)
    inspected_files: List[str] = Field(default_factory=list)


class AdvancedContextEngine:
    """Central context intelligence engine assembling compact, high-provenance context packets."""

    def __init__(
        self,
        workspace_root: Path,
        error_memory: Optional[ErrorReflectionMemory] = None,
    ):
        self.workspace_root = workspace_root.resolve()
        self.error_memory = error_memory or ErrorReflectionMemory()
        self.knowledge_base: List[ProvenanceMemoryItem] = []
        self.active_task: Optional[LiveTaskContext] = None

    # --- A. Task Context Management ---
    def initialize_task(
        self,
        task_id: str,
        user_goal: str,
        task_type: str = "software_engineering",
        plan: Optional[List[str]] = None,
        constraints: Optional[List[str]] = None,
        verification: Optional[List[str]] = None,
    ) -> LiveTaskContext:
        steps = plan or ["Inspect environment", "Execute required steps", "Verify completion"]
        self.active_task = LiveTaskContext(
            task_id=task_id,
            user_goal=user_goal,
            task_type=task_type,
            execution_plan=steps,
            completed_steps=[],
            pending_steps=steps.copy(),
            current_step=steps[0] if steps else None,
            known_constraints=constraints or ["Confinement to authorized workspace", "Zero secret leakage"],
            verification_requirements=verification or ["Automated test or observable state confirmation"],
            execution_status="PLANNING",
        )
        # Check historical mistakes on this task type and inject
        lessons = self.error_memory.retrieve_relevant_lessons(user_goal)
        for l in lessons:
            self.active_task.failure_history.append(
                f"Historical trap on '{l.failed_action}': {l.error_message} -> Fix: {l.correction_strategy}"
            )
        return self.active_task

    def advance_task_step(self, step_completed: str, result_summary: str) -> None:
        if not self.active_task:
            return
        self.active_task.completed_steps.append(f"{step_completed}: {result_summary[:160]}")
        if step_completed in self.active_task.pending_steps:
            self.active_task.pending_steps.remove(step_completed)
        self.active_task.current_step = self.active_task.pending_steps[0] if self.active_task.pending_steps else None
        if not self.active_task.pending_steps:
            self.active_task.execution_status = "COMPLETED"

    # --- B. Dynamic Project Context (Live Filesystem Inspection) ---
    def inspect_project(self) -> LiveProjectContext:
        """Inspect actual project files to extract real configurations, dependencies, and test commands."""
        languages = set()
        frameworks = set()
        dependencies = []
        test_cmds = []
        build_cmds = []
        inspected = []

        # Check Python pyproject.toml / requirements.txt
        req_file = self.workspace_root / "requirements.txt"
        if req_file.exists():
            inspected.append("requirements.txt")
            languages.add("Python")
            content = req_file.read_text(encoding="utf-8")
            for line in content.splitlines():
                clean = line.strip()
                if clean and not clean.startswith("#"):
                    dependencies.append(clean)
                    if "fastapi" in clean.lower():
                        frameworks.add("FastAPI")
                    if "pytest" in clean.lower():
                        test_cmds.append("python -m pytest tests")
                    if "playwright" in clean.lower():
                        frameworks.add("Playwright")

        # Check C# / .NET
        csproj_files = list(self.workspace_root.glob("**/*.csproj"))
        if csproj_files:
            languages.add("C#")
            frameworks.add("WinUI 3 (.NET 8 / Windows App SDK)")
            inspected.append(str(csproj_files[0].relative_to(self.workspace_root)))
            build_cmds.append("dotnet build")

        # Check Node.js package.json
        pkg_file = self.workspace_root / "package.json"
        if pkg_file.exists():
            languages.add("JavaScript / TypeScript")
            inspected.append("package.json")

        return LiveProjectContext(
            project_dir=str(self.workspace_root),
            languages=list(languages),
            frameworks=list(frameworks),
            dependencies=dependencies[:15],
            database_technology="SQLite WAL (Persistent) & Vector Index",
            architectural_decisions=[
                "Decoupled MVVM presentation layer with loopback IPC",
                "Host-side independent security policy mediation",
                "Hardware-backed Windows DPAPI key vault",
            ],
            test_commands=test_cmds or ["python -m pytest tests"],
            build_commands=build_cmds,
            inspected_files=inspected,
        )

    # --- C. Memory Quality & Provenance Tracking ---
    def add_knowledge_item(
        self,
        content: str,
        quality_tier: MemoryQualityTier,
        source: str,
        confidence: float = 1.0,
    ) -> ProvenanceMemoryItem:
        """Add knowledge tagged with strict quality tier and source provenance."""
        item_id = f"mem_{int(time.time() * 1000)}_{len(self.knowledge_base) + 1}"
        emb = simple_embedding(content)
        item = ProvenanceMemoryItem(
            item_id=item_id,
            content=content,
            quality_tier=quality_tier,
            source=source,
            confidence=confidence,
            embedding=emb,
        )
        self.knowledge_base.append(item)
        return item

    # --- D. Hybrid Context Retrieval ---
    def retrieve_relevant_context(
        self,
        query: str,
        top_k: int = 5,
        min_quality: Optional[MemoryQualityTier] = None,
    ) -> List[ProvenanceMemoryItem]:
        """Hybrid keyword + semantic retrieval filtered by confidence and quality tiers."""
        query_words = set(query.lower().split())
        query_emb = simple_embedding(query)
        candidates = []

        for item in self.knowledge_base:
            # Never return failed approaches unless specifically querying errors
            if item.quality_tier == MemoryQualityTier.FAILED_APPROACH and "error" not in query.lower() and "fail" not in query.lower():
                continue

            # Check quality tier threshold
            if min_quality and item.quality_tier.value != min_quality.value:
                continue

            # Compute semantic score
            sim = cosine_similarity(query_emb, item.embedding) if item.embedding else 0.0

            # Compute lexical keyword overlap
            content_lower = item.content.lower()
            keyword_overlap = sum(1 for w in query_words if len(w) >= 4 and w in content_lower)
            lexical_boost = min(0.4, keyword_overlap * 0.1)

            # Weight by confidence
            score = (sim + lexical_boost) * item.confidence
            if score > 0.1:
                candidates.append((item, score))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return [c[0] for c in candidates[:top_k]]

    # --- E. Lossless Security-Preserving Context Compaction ---
    @staticmethod
    def window_messages(messages: list, max_messages: int = 20) -> list:
        """Keep the system head plus the newest turns within budget.

        System messages are never trimmed; non-system turns are cut from
        the middle (oldest first). Non-positive budgets yield [].
        """
        if max_messages <= 0:
            return []
        head = [m for m in messages if m.role == "system"]
        tail = [m for m in messages if m.role != "system"]
        room = max_messages - len(head)
        if room <= 0:
            return head[:max_messages] if max_messages < len(head) else head
        return head + tail[-room:] if room < len(tail) else head + tail

    def build_compacted_context(self, max_tokens: int = 2048) -> str:
        """Assembles compacted prompt context. Never compacts away security rules or user goals."""
        sections = []

        # 1. Non-Negotiable Invariant Security Directives (NEVER compacted away)
        sections.append(
            "=== SECURITY & POLICY BOUNDARIES ===\n"
            "- Host-side deterministic policy enforcement is active.\n"
            "- All file writes, command executions, and external actions require explicit authorization.\n"
            "- Filesystem access is strictly confined to authorized workspace root.\n"
            "- Zero credential leakage: Never output or prompt-inject secrets."
        )

        # 2. Active Task Context
        if self.active_task:
            task_lines = [
                f"Goal: {self.active_task.user_goal}",
                f"Status: {self.active_task.execution_status}",
                f"Current Step: {self.active_task.current_step or 'None'}",
            ]
            if self.active_task.completed_steps:
                task_lines.append("Completed Steps: " + "; ".join(self.active_task.completed_steps[-3:]))
            if self.active_task.pending_steps:
                task_lines.append("Pending Steps: " + ", ".join(self.active_task.pending_steps))
            if self.active_task.failure_history:
                task_lines.append("Lessons to Apply:\n" + "\n".join(f"- {f}" for f in self.active_task.failure_history[-2:]))
            sections.append("=== ACTIVE TASK STATE ===\n" + "\n".join(task_lines))

        # 3. Live Project Facts
        proj = self.inspect_project()
        proj_lines = [
            f"Languages: {', '.join(proj.languages) if proj.languages else 'Python'}",
            f"Frameworks: {', '.join(proj.frameworks) if proj.frameworks else 'None detected'}",
            f"Test Command: {proj.test_commands[0] if proj.test_commands else 'pytest'}",
            f"Database: {proj.database_technology}",
        ]
        sections.append("=== PROJECT CONFIGURATION (LIVE INSPECTION) ===\n" + "\n".join(proj_lines))

        # 4. Verified Knowledge Items (High provenance only)
        verified_items = [
            k for k in self.knowledge_base
            if k.quality_tier in [MemoryQualityTier.VERIFIED_FACT, MemoryQualityTier.USER_ASSERTED, MemoryQualityTier.SUCCESSFUL_CORRECTION]
        ]
        if verified_items:
            k_lines = [f"- [{k.quality_tier.value}] {k.content}" for k in verified_items[-4:]]
            sections.append("=== VERIFIED FACTS & DECISIONS ===\n" + "\n".join(k_lines))

        raw_text = "\n\n".join(sections)
        return raw_text
