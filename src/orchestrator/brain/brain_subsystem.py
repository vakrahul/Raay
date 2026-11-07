"""Unified Contextual Super Brain Subsystem (Stage IV)."""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from src.orchestrator.brain.models import MemoryEntry, MemoryType, VerificationStatus
from src.orchestrator.brain.working_memory import WorkingMemory
from src.orchestrator.brain.persistent_store import PersistentMemoryStore
from src.orchestrator.brain.semantic_memory import SemanticMemory
from src.orchestrator.brain.project_memory import ProjectMemory
from src.orchestrator.brain.context_prioritizer import ContextPrioritizer


class BrainSubsystem:
    """Central manager coordinating all memory tiers and user memory controls."""

    def __init__(self, workspace_root: Path, db_path: Optional[Path] = None):
        self.workspace_root = workspace_root.resolve()
        self.working = WorkingMemory(session_id="default_session")
        self.persistent = PersistentMemoryStore(db_path=db_path)
        self.semantic = SemanticMemory()
        self.project = ProjectMemory(workspace_root=self.workspace_root)
        self.prioritizer = ContextPrioritizer()

    def tier_names(self) -> list:
        """Return the active memory tier names for diagnostics (no content)."""
        return ["working", "persistent", "semantic", "project", "prioritizer"]

    def assemble_context(self, query: str, max_tokens: int = 4096) -> str:
        """Retrieve and prioritize context across all tiers for an incoming query."""
        working_ctx = self.working.get_context_summary()
        project_ctx = self.project.get_summary()
        semantic_matches = self.semantic.retrieve_similar(query, top_k=5)
        episodic_history = self.persistent.search_by_type(MemoryType.EPISODIC, limit=5)

        return self.prioritizer.prioritize_context(
            working_context=working_ctx,
            project_context=project_ctx,
            semantic_entries=semantic_matches,
            episodic_entries=episodic_history,
            max_prompt_tokens=max_tokens,
        )

    # --- Phase 40: User Memory Controls ---

    def memory_stats(self) -> Dict[str, int]:
        """Return per-tier memory counts for dashboard cards (no content)."""
        counts: Dict[str, int] = {t.value: 0 for t in MemoryType}
        for entry in self.inspect_memory():
            counts[entry.memory_type.value] = counts.get(entry.memory_type.value, 0) + 1
        counts["total"] = sum(v for k, v in counts.items() if k != "total")
        return counts

    def inspect_memory(self, memory_type: Optional[MemoryType] = None) -> List[MemoryEntry]:
        """Inspect stored memories, optionally filtered by type."""
        all_entries = self.persistent.get_all() + self.semantic.get_all()
        if memory_type:
            return [e for e in all_entries if e.memory_type == memory_type]
        return all_entries

    def correct_memory(self, entry_id: str, new_content: str) -> bool:
        """Allow user to edit or correct an existing memory fact."""
        # Check persistent
        entry = self.persistent.get_entry(entry_id)
        if entry:
            entry.content = new_content
            entry.verification_status = VerificationStatus.USER_ASSERTED
            self.persistent.save_entry(entry)
            return True
        # Check semantic
        if entry_id in self.semantic._entries:
            sem_entry = self.semantic._entries[entry_id]
            sem_entry.content = new_content
            sem_entry.verification_status = VerificationStatus.USER_ASSERTED
            self.semantic.store_fact(entry_id, new_content)
            return True
        return False

    def export_json(self) -> str:
        """Export complete memory store to portable JSON."""
        entries = self.inspect_memory()
        return json.dumps([e.model_dump() for e in entries], indent=2)

    def delete_memory(self, entry_id: str) -> bool:
        """Delete specific memory entry."""
        deleted_persistent = self.persistent.delete_entry(entry_id)
        deleted_semantic = self.semantic.delete_fact(entry_id)
        return deleted_persistent or deleted_semantic

    def clear_all(self) -> None:
        """Permanently clear all stored memories."""
        self.working.clear()
        self.persistent.clear()
        self.semantic._entries.clear()
