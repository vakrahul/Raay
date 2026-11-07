"""Project Memory: Scoped workspace architecture notes, directory rules, and metadata."""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from src.orchestrator.brain.models import MemoryEntry, MemoryType, VerificationStatus


class ProjectMemory:
    """Manages project-specific contextual knowledge tied to a workspace folder."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self.context_file = self.workspace_root / ".winai" / "project-context.json"
        self._cache: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if self.context_file.exists():
            try:
                self._cache = json.loads(self.context_file.read_text(encoding="utf-8"))
            except Exception:
                self._cache = {}
        else:
            self._cache = {
                "project_name": self.workspace_root.name,
                "architecture_notes": [],
                "important_files": [],
                "conventions": [],
            }

    def save(self) -> None:
        self.context_file.parent.mkdir(parents=True, exist_ok=True)
        self.context_file.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")

    def add_architecture_note(self, note: str) -> None:
        notes = self._cache.setdefault("architecture_notes", [])
        if note not in notes:
            notes.append(note)
            self.save()

    def add_important_file(self, file_path: str, description: str) -> None:
        files = self._cache.setdefault("important_files", [])
        files.append({"path": file_path, "description": description})
        self.save()

    def get_summary(self) -> str:
        lines = [f"=== Project: {self._cache.get('project_name', 'Workspace')} ==="]
        notes = self._cache.get("architecture_notes", [])
        if notes:
            lines.append("Architecture Notes:")
            for n in notes:
                lines.append(f"- {n}")
        files = self._cache.get("important_files", [])
        if files:
            lines.append("Key Files:")
            for f in files:
                lines.append(f"- {f['path']}: {f['description']}")
        return "\n".join(lines)
