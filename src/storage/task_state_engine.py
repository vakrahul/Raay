"""Persistent Task State Engine with SQLite WAL and Crash Resumption (Module 3)."""
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class StepRecord(BaseModel):
    step_id: str
    title: str
    description: str
    status: str = "PENDING"  # PENDING, IN_PROGRESS, COMPLETED, FAILED, AWAITING_APPROVAL
    result: Optional[str] = None
    error: Optional[str] = None
    approval_status: Optional[str] = None
    completed_at: Optional[float] = None


class PersistentTaskState(BaseModel):
    task_id: str
    objective: str
    requirements: List[str] = Field(default_factory=list)
    current_step_index: int = 0
    steps: List[StepRecord] = Field(default_factory=list)
    decisions: Dict[str, str] = Field(default_factory=dict)
    test_results: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    @property
    def current_step(self) -> Optional[StepRecord]:
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    @property
    def is_completed(self) -> bool:
        return all(s.status == "COMPLETED" for s in self.steps) if self.steps else False

    def get_step_relevant_context(self) -> str:
        """Token-efficient context extraction: returns only info needed for the current step."""
        lines = [f"Objective: {self.objective}"]
        if self.current_step:
            lines.append(f"Active Step [{self.current_step_index + 1}/{len(self.steps)}]: {self.current_step.title}")
            lines.append(f"Description: {self.current_step.description}")

        # Add outputs of previous completed steps only
        completed = [s for s in self.steps if s.status == "COMPLETED" and s.result]
        if completed:
            lines.append("Completed Steps Context:")
            for s in completed[-3:]:  # Keep last 3 completed steps max to save tokens
                lines.append(f"- {s.title}: {s.result[:180]}")

        if self.decisions:
            lines.append(f"Key Architectural Decisions: {json.dumps(self.decisions)}")

        return "\n".join(lines)


class TaskStateEngine:
    """Manages transactional task persistence and crash recovery using SQLite WAL."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = (db_path or Path.home() / ".winai" / "task_state.db").resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_states (
                    task_id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    requirements TEXT NOT NULL,
                    current_step_index INTEGER NOT NULL,
                    steps TEXT NOT NULL,
                    decisions TEXT NOT NULL,
                    test_results TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
            """)
            conn.commit()

    def save_task_state(self, task: PersistentTaskState) -> None:
        task.updated_at = time.time()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO task_states (
                    task_id, objective, requirements, current_step_index,
                    steps, decisions, test_results, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                task.task_id,
                task.objective,
                json.dumps(task.requirements),
                task.current_step_index,
                json.dumps([s.model_dump() for s in task.steps]),
                json.dumps(task.decisions),
                json.dumps(task.test_results),
                task.created_at,
                task.updated_at,
            ))
            conn.commit()

    def load_task_state(self, task_id: str) -> Optional[PersistentTaskState]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM task_states WHERE task_id = ?;", (task_id,))
            row = cur.fetchone()
            if not row:
                return None
            return PersistentTaskState(
                task_id=row["task_id"],
                objective=row["objective"],
                requirements=json.loads(row["requirements"]),
                current_step_index=row["current_step_index"],
                steps=[StepRecord(**s) for s in json.loads(row["steps"])],
                decisions=json.loads(row["decisions"]),
                test_results=json.loads(row["test_results"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def resume_task(self, task_id: str) -> Optional[PersistentTaskState]:
        """Find the exact step where an interrupted task paused and advance ready for execution."""
        task = self.load_task_state(task_id)
        if not task:
            return None

        # Find first step that is not completed
        for idx, step in enumerate(task.steps):
            if step.status != "COMPLETED":
                task.current_step_index = idx
                step.status = "IN_PROGRESS"
                self.save_task_state(task)
                return task

        return task
