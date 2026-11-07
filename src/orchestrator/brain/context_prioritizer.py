"""Context Prioritization and Token Budget Allocator (Phase 38)."""
from typing import Dict, List, Tuple
from src.orchestrator.brain.models import MemoryEntry, MemoryType, VerificationStatus


def estimate_tokens(text: str) -> int:
    """Rough heuristic token estimator (~4 characters per token)."""
    return max(1, len(text) // 4)


class ContextPrioritizer:
    """Budgets and prioritizes multi-tier memory to prevent context window overflow."""

    def __init__(self, total_context_limit: int = 8192):
        self.total_limit = total_context_limit
        # Budget fractions
        self.working_fraction = 0.50
        self.project_fraction = 0.20
        self.semantic_fraction = 0.20
        self.episodic_fraction = 0.10

    def prioritize_context(
        self,
        working_context: str,
        project_context: str,
        semantic_entries: List[Tuple[MemoryEntry, float]],
        episodic_entries: List[MemoryEntry],
        max_prompt_tokens: int = 4096,
    ) -> str:
        """Assemble prioritized, pruned context within token budget."""
        sections = []

        # 1. Working memory has highest priority
        working_budget = int(max_prompt_tokens * self.working_fraction)
        if working_context:
            truncated_working = working_context[: working_budget * 4]
            sections.append(f"## Active Working Context\n{truncated_working}")

        # 2. Project memory
        project_budget = int(max_prompt_tokens * self.project_fraction)
        if project_context:
            truncated_project = project_context[: project_budget * 4]
            sections.append(f"## Project Guidelines\n{truncated_project}")

        # 3. Relevant semantic facts (ranked by score)
        semantic_budget = int(max_prompt_tokens * self.semantic_fraction)
        semantic_text_blocks = []
        used_sem_tokens = 0
        for entry, score in semantic_entries:
            block = f"- [{entry.verification_status.value}] {entry.content} (relevance: {score:.2f})"
            block_tokens = estimate_tokens(block)
            if used_sem_tokens + block_tokens > semantic_budget:
                break
            semantic_text_blocks.append(block)
            used_sem_tokens += block_tokens

        if semantic_text_blocks:
            sections.append("## Retrieved Semantic Facts\n" + "\n".join(semantic_text_blocks))

        # 4. Episodic task history
        episodic_budget = int(max_prompt_tokens * self.episodic_fraction)
        episodic_text_blocks = []
        used_epi_tokens = 0
        for entry in episodic_entries:
            block = f"- {entry.content}"
            block_tokens = estimate_tokens(block)
            if used_epi_tokens + block_tokens > episodic_budget:
                break
            episodic_text_blocks.append(block)
            used_epi_tokens += block_tokens

        if episodic_text_blocks:
            sections.append("## Past Task Summaries\n" + "\n".join(episodic_text_blocks))

        return "\n\n".join(sections)
