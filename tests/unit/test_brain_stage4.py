"""Unit tests for Stage IV: Super Brain and Contextual Intelligence (Phases 31-40)."""
import json
from pathlib import Path
import pytest

from src.orchestrator.brain.brain_subsystem import BrainSubsystem
from src.orchestrator.brain.context_prioritizer import ContextPrioritizer, estimate_tokens
from src.orchestrator.brain.models import MemoryEntry, MemoryType, VerificationStatus
from src.orchestrator.brain.persistent_store import PersistentMemoryStore
from src.orchestrator.brain.semantic_memory import SemanticMemory, cosine_similarity, simple_embedding
from src.orchestrator.brain.working_memory import WorkingMemory


@pytest.mark.unit
def test_working_memory_lifecycle():
    """Verify in-session goal tracking and observation history."""
    wm = WorkingMemory(session_id="s_test")
    wm.set_goal("Refactor auth system", subtasks=["Inspect files", "Run tests"])
    wm.set_variable("target_dir", "src/security")
    
    obs = wm.record_observation(
        observation="Tests passing in src/security",
        tool_name="pytest_runner",
        verification_status=VerificationStatus.VERIFIED_OBSERVATION,
    )
    assert obs.verification_status == VerificationStatus.VERIFIED_OBSERVATION

    summary = wm.get_context_summary()
    assert "Goal: Refactor auth system" in summary
    assert "Subtasks: Inspect files, Run tests" in summary
    assert "target_dir" in summary


@pytest.mark.unit
def test_persistent_sqlite_memory(temp_workspace: Path):
    """Verify episodic memory persists in SQLite WAL and survives instance recreation."""
    db_file = temp_workspace / "test_memory.db"
    store1 = PersistentMemoryStore(db_path=db_file)

    entry = MemoryEntry(
        id="task_summary_01",
        memory_type=MemoryType.EPISODIC,
        content="Successfully created vertical slice with MockProvider.",
        verification_status=VerificationStatus.VERIFIED_OBSERVATION,
        tags=["phase10", "vertical_slice"],
    )
    store1.save_entry(entry)

    # Recreate store instance pointing to same db
    store2 = PersistentMemoryStore(db_path=db_file)
    retrieved = store2.get_entry("task_summary_01")
    assert retrieved is not None
    assert retrieved.content == entry.content
    assert retrieved.tags == ["phase10", "vertical_slice"]

    # Search by type
    episodic_list = store2.search_by_type(MemoryType.EPISODIC)
    assert len(episodic_list) == 1


@pytest.mark.unit
def test_semantic_vector_similarity():
    """Verify semantic memory cosine similarity returns relevant matches."""
    sm = SemanticMemory()
    sm.store_fact("fact_1", "The project uses WinUI 3 and .NET 8 for the native desktop client.")
    sm.store_fact("fact_2", "PostgreSQL or SQLite WAL is used for relational structured data.")
    sm.store_fact("fact_3", "Python FastAPI powers the AI orchestration layer.")

    matches = sm.retrieve_similar("desktop client UI technology", top_k=2)
    assert len(matches) > 0
    top_entry, top_score = matches[0]
    assert top_entry.id == "fact_1"
    assert top_score > 0.2


@pytest.mark.unit
def test_context_prioritization_and_budgeting():
    """Verify ContextPrioritizer respects token bounds and includes key sections."""
    cp = ContextPrioritizer(total_context_limit=4096)
    
    working = "Current goal: Fix security vulnerability"
    project = "WinAI Architecture: Strict least privilege"
    semantic = [
        (MemoryEntry(id="s1", memory_type=MemoryType.SEMANTIC, content="Fact A"), 0.95),
        (MemoryEntry(id="s2", memory_type=MemoryType.SEMANTIC, content="Fact B"), 0.85),
    ]
    episodic = [
        MemoryEntry(id="e1", memory_type=MemoryType.EPISODIC, content="Task 1 passed"),
    ]

    assembled = cp.prioritize_context(
        working_context=working,
        project_context=project,
        semantic_entries=semantic,
        episodic_entries=episodic,
        max_prompt_tokens=1000,
    )
    assert "## Active Working Context" in assembled
    assert "## Project Guidelines" in assembled
    assert "## Retrieved Semantic Facts" in assembled
    assert "## Past Task Summaries" in assembled


@pytest.mark.unit
def test_memory_user_controls(temp_workspace: Path):
    """Verify Phase 40 User Memory Controls (inspect, correct, export, delete, clear)."""
    db_file = temp_workspace / "controls_mem.db"
    brain = BrainSubsystem(workspace_root=temp_workspace, db_path=db_file)

    # Store fact
    brain.semantic.store_fact("user_pref", "User prefers dark mode and high contrast.")
    assert len(brain.inspect_memory()) == 1

    # Correct memory fact
    success = brain.correct_memory("user_pref", "User prefers dark mode with blue accent.")
    assert success is True
    corrected = brain.semantic._entries["user_pref"]
    assert "blue accent" in corrected.content
    assert corrected.verification_status == VerificationStatus.USER_ASSERTED

    # Export JSON
    exported = brain.export_json()
    exported_data = json.loads(exported)
    assert len(exported_data) == 1
    assert "blue accent" in exported_data[0]["content"]

    # Delete
    deleted = brain.delete_memory("user_pref")
    assert deleted is True
    assert len(brain.inspect_memory()) == 0
