"""Persistent SQLite storage for episodic and long-term memory."""
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional
from src.orchestrator.brain.models import MemoryEntry, MemoryType, VerificationStatus


class PersistentMemoryStore:
    """Manages long-term structured memory surviving application restarts."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = (db_path or Path.home() / ".winai" / "memory.db").resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        # Enable Write-Ahead Logging (WAL) for high concurrency and performance
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    embedding TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(memory_type);")
            conn.commit()

    def save_entry(self, entry: MemoryEntry) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO memories (
                    id, memory_type, content, verification_status, tags, metadata, embedding, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                entry.id,
                entry.memory_type.value,
                entry.content,
                entry.verification_status.value,
                json.dumps(entry.tags),
                json.dumps(entry.metadata),
                json.dumps(entry.embedding) if entry.embedding else None,
                entry.created_at,
                entry.updated_at,
            ))
            conn.commit()

    def get_entry(self, entry_id: str) -> Optional[MemoryEntry]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM memories WHERE id = ?;", (entry_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_entry(row)

    def search_by_type(self, memory_type: MemoryType, limit: int = 50) -> List[MemoryEntry]:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM memories WHERE memory_type = ? ORDER BY created_at DESC LIMIT ?;",
                (memory_type.value, limit),
            )
            return [self._row_to_entry(r) for r in cur.fetchall()]

    def count_by_type(self, memory_type: MemoryType) -> int:
        """Return the record count for a memory tier without loading content."""
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) AS n FROM memories WHERE memory_type = ?;",
                (memory_type.value,),
            )
            return int(cur.fetchone()["n"])

    def delete_entry(self, entry_id: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("DELETE FROM memories WHERE id = ?;", (entry_id,))
            conn.commit()
            return cur.rowcount > 0

    def get_all(self) -> List[MemoryEntry]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM memories ORDER BY created_at DESC;")
            return [self._row_to_entry(r) for r in cur.fetchall()]

    def clear(self) -> None:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM memories;")
            conn.commit()

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            id=row["id"],
            memory_type=MemoryType(row["memory_type"]),
            content=row["content"],
            verification_status=VerificationStatus(row["verification_status"]),
            tags=json.loads(row["tags"]),
            metadata=json.loads(row["metadata"]),
            embedding=json.loads(row["embedding"]) if row["embedding"] else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
