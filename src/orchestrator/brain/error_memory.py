"""Error reflection and mistake learning subsystem.

Stores historical failure traces, root causes, and corrective strategies in SQLite.
Injects relevant historical lessons into active agent contexts to prevent repeating past errors.
"""

from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.orchestrator.brain.semantic_memory import cosine_similarity, simple_embedding


class ErrorReflectionRecord(BaseModel):
    error_id: str
    task_type: str
    failed_action: str
    error_message: str
    root_cause: str
    correction_strategy: str
    embedding: Optional[List[float]] = None
    timestamp: float = Field(default_factory=time.time)


class ErrorReflectionMemory:
    """Manages transactional persistence and semantic retrieval of past error reflections."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = (db_path or Path.home() / ".winai" / "error_reflections.db").resolve()
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
                CREATE TABLE IF NOT EXISTS error_reflections (
                    error_id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    failed_action TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    root_cause TEXT NOT NULL,
                    correction_strategy TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );
            """)
            conn.commit()

    def record_mistake(
        self,
        task_type: str,
        failed_action: str,
        error_message: str,
        root_cause: str,
        correction_strategy: str,
    ) -> ErrorReflectionRecord:
        """Store an analyzed mistake and its corrective solution."""
        error_id = f"err_{int(time.time() * 1000)}"
        record = ErrorReflectionRecord(
            error_id=error_id,
            task_type=task_type,
            failed_action=failed_action,
            error_message=error_message,
            root_cause=root_cause,
            correction_strategy=correction_strategy,
            embedding=simple_embedding(f"{task_type} {failed_action} {error_message}"),
        )

        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO error_reflections (
                    error_id, task_type, failed_action, error_message, root_cause, correction_strategy, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (
                record.error_id,
                record.task_type,
                record.failed_action,
                record.error_message,
                record.root_cause,
                record.correction_strategy,
                record.timestamp,
            ))
            conn.commit()

        return record

    def retrieve_relevant_lessons(self, query: str, top_k: int = 3) -> List[ErrorReflectionRecord]:
        """Find past errors semantically similar to the current task query."""
        query_emb = simple_embedding(query)
        candidates = []

        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM error_reflections ORDER BY timestamp DESC LIMIT 50;")
            rows = cur.fetchall()

        query_words = set(query.lower().split())
        for r in rows:
            record_text = f"{r['task_type']} {r['failed_action']} {r['error_message']}".lower()
            emb = simple_embedding(record_text)
            sim = cosine_similarity(query_emb, emb)
            lexical_match = any(w[:4] in record_text for w in query_words if len(w) >= 4)
            score = sim + (0.5 if lexical_match else 0.0)
            if score > 0.0:
                candidates.append((
                    ErrorReflectionRecord(
                        error_id=r["error_id"],
                        task_type=r["task_type"],
                        failed_action=r["failed_action"],
                        error_message=r["error_message"],
                        root_cause=r["root_cause"],
                        correction_strategy=r["correction_strategy"],
                        timestamp=r["timestamp"],
                        embedding=emb,
                    ),
                    score
                ))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return [item[0] for item in candidates[:top_k] if item[1] > 0.0]

    def format_reflection_prompt(self, task_query: str) -> str:
        """Format historical lessons into a compact prompt block to avoid repeating mistakes."""
        lessons = self.retrieve_relevant_lessons(task_query)
        if not lessons:
            return ""

        lines = ["\n[CRITICAL LESSONS FROM PAST MISTAKES - DO NOT REPEAT]"]
        for idx, l in enumerate(lessons, 1):
            lines.append(
                f"{idx}. Past failure on '{l.failed_action}': {l.error_message}\n"
                f"   Root Cause: {l.root_cause}\n"
                f"   Required Fix: {l.correction_strategy}"
            )
        return "\n".join(lines)
